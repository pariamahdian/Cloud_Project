import base64
from flask import Flask, request, jsonify, json
from kubernetes import client, config
import random
import string
import yaml

app = Flask(__name__)
config.load_kube_config()


@app.route('/addapplication', methods=['POST'])
def add_application():
    data = request.json

    app_name = data['AppName']
    replicas = data['Replicas']
    image_address = data['ImageAddress']
    image_tag = data['ImageTag']
    domain_address = data['DomainAddress']
    service_port = data['ServicePort']
    resources = data['Resources']
    envs = data['Envs']
    secrets = [env for env in envs if env.get('IsSecret')]
    external_access = data.get('ExternalAccess', False)
    monitor = data.get('Monitor', False)

    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    networking_v1 = client.NetworkingV1Api()

    for secret_env in secrets:
        secret_name = f"{app_name}-secret-{secret_env['Key'].lower()}"
        secret_data = {secret_env['Key']: secret_env['Value']}
        create_secret(secret_name, secret_data)

    create_deployment(app_name, replicas, image_address, image_tag, resources, envs, secrets, monitor)

    create_service(app_name, service_port)

    if external_access:
        create_ingress(app_name, domain_address, service_port)
    return jsonify({"message": "Application created successfully"}), 201


def create_secret(name, data):
    v1 = client.CoreV1Api()
    secret = client.V1Secret(metadata=client.V1ObjectMeta(name=name),
                             data={k: base64.b64encode(v.encode()).decode() for k, v in data.items()
                                   }
                             )
    v1.create_namespaced_secret(namespace="default", body=secret)


def create_deployment(name, replicas, image_address, image_tag, resources, envs, secrets, monitor):
    apps_v1 = client.AppsV1Api()
    containers = []
    volume_mounts = []
    volumes = []

    for env in envs:
        if not env.get('IsSecret'):
            containers.append(client.V1Container(
                name=name,
                image=f"{image_address}:{image_tag}",
                ports=[client.V1ContainerPort(container_port=80)],
                env=[client.V1EnvVar(name=env['Key'], value=env['Value'])],
                resources=client.V1ResourceRequirements(
                    requests={"cpu": resources['CPU'], "memory": resources['RAM']}
                )
            ))
        else:
            secret_env = next((secret for secret in secrets if secret['Key'].lower() == env['Key'].lower()), None)
            if secret_env:
                secret_name = f"{name}-secret-{secret_env['Key'].lower()}"
                volume_mount = client.V1VolumeMount(
                    name=secret_name,
                    mount_path=f"/mnt/secrets/{secret_env['Key'].lower()}",
                    read_only=True
                )
                volume_mounts.append(volume_mount)
                volumes.append(
                    client.V1Volume(name=secret_name, secret=client.V1SecretVolumeSource(secret_name=secret_name)
                                    )
                )

    labels = {"app": name}
    if monitor:
        create_cronjob_from_yaml()
        labels["monitor"] = "true"

    spec = client.V1DeploymentSpec(
        replicas=replicas,
        template=client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels=labels),
            spec=client.V1PodSpec(
                containers=containers,
                volumes=volumes
            )
        ),
        selector={'matchLabels': {'app': name}}
    )

    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(name=name),
        spec=spec
    )

    apps_v1.create_namespaced_deployment(namespace="default", body=deployment)


def create_service(name, port):
    v1 = client.CoreV1Api()
    service = client.V1Service(metadata=client.V1ObjectMeta(name=name),
                               spec=client.V1ServiceSpec(selector={"app": name},
                                                         ports=[client.V1ServicePort(port=port, target_port=80)]
                                                         )
                               )
    v1.create_namespaced_service(namespace="default", body=service)


def create_ingress(name, domain, port):
    networking_v1 = client.NetworkingV1Api()
    ingress = client.V1Ingress(metadata=client.V1ObjectMeta(name=name),
                               spec=client.V1IngressSpec(
                                   rules=[client.V1IngressRule(
                                       host=domain,
                                       http=client.V1HTTPIngressRuleValue(
                                           paths=[client.V1HTTPIngressPath(
                                               path="/",
                                               path_type="Prefix",
                                               backend=client.V1IngressBackend(
                                                   service=client.V1IngressServiceBackend(
                                                       name=name,
                                                       port=client.V1ServiceBackendPort(number=port)
                                                   )
                                               )
                                           )
                                           ]
                                       )
                                   )
                                   ]
                               )
                               )
    networking_v1.create_namespaced_ingress(namespace="default", body=ingress)


@app.route('/getstatus', methods=['GET'])
def get_status():
    data = request.json
    app_name = data['AppName']
    print(f"Received request for app: {app_name}")

    apps_v1 = client.AppsV1Api()
    v1 = client.CoreV1Api()

    try:
        # Get deployment status
        deployment = apps_v1.read_namespaced_deployment(name=app_name, namespace='default')
        replicas = deployment.spec.replicas
        ready_replicas = deployment.status.ready_replicas
        # Get pod statuses
        pod_list = v1.list_namespaced_pod(namespace='default', label_selector=f'app={app_name}')
        pod_statuses = []
        for pod in pod_list.items:
            pod_status = {
                'Name': pod.metadata.name,
                'Phase': pod.status.phase,
                'HostIP': pod.status.host_ip,
                'PodIP': pod.status.pod_ip,
                'StartTime': pod.status.start_time}
            pod_statuses.append(pod_status)
            response = {
                'DeploymentName': app_name,
                'Replicas': replicas,
                'ReadyReplicas': ready_replicas,
                'PodStatuses': pod_statuses}
            return jsonify(response), 200
    except client.exceptions.ApiException as e:
        print(f"error: {e}")
        return jsonify({"error": f"Deployment {app_name} not found"}), 404


@app.route('/getallapplicationstatus', methods=['GET'])
def get_all_application_status():
    v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()

    try:
        deployments = v1.list_namespaced_deployment(namespace="default")

        all_statuses = []
        for deployment in deployments.items:
            app_name = deployment.metadata.name
            pods = core_v1.list_namespaced_pod("default", label_selector=f'app={app_name}')

            pod_statuses = []
            for pod in pods.items:
                pod_statuses.append({
                    "Name": pod.metadata.name,
                    "Phase": pod.status.phase,
                    "HostIP": pod.status.host_ip,
                    "PodIP": pod.status.pod_ip,
                    "StartTime": pod.status.start_time.strftime("%Y-%m-%dT%H:%M:%SZ") if pod.status.start_time else None
                })

            deployment_status = {
                "DeploymentName": app_name,
                "Replicas": deployment.spec.replicas,
                "ReadyReplicas": deployment.status.ready_replicas,
                "PodStatuses": pod_statuses
            }
            all_statuses.append(deployment_status)

        return jsonify(all_statuses), 200

    except client.exceptions.ApiException as e:
        return jsonify({"error": "An error occurred while retrieving the deployment statuses"}), 500


@app.route('/deploypostgres', methods=['POST'])
def deploy_postgres_application():
    data = request.json

    app_name = data['AppName']
    resources = data['Resources']
    external_access = data.get('External', False)

    # Automatically generate and store user information using Kubernetes Secrets
    username = generate_random_username()
    password = generate_random_password()
    create_user_secret(app_name, username, password)

    # Use ConfigMaps to manage PostgreSQL settings
    postgres_settings = {
        "shared_buffers": "128MB",
        "max_connections": "100"
        # Add other configurations as needed
    }
    create_config_map(app_name, postgres_settings)

    # Create StatefulSet and internal service
    create_stateful_set(app_name, resources)
    create_service(app_name, 5432)  # Typical PostgreSQL port
    if external_access:
        create_ingress(app_name, 'postgres.example.com', 5432)  # Assuming external access

    return jsonify({"message": f"PostgreSQL application '{app_name}' deployed successfully"}), 201


def generate_random_username():
    return ''.join(random.choice(string.ascii_lowercase) for i in range(8))


def generate_random_password():
    characters = string.ascii_letters + string.digits + string.punctuation
    return ''.join(random.choice(characters) for i in range(12))


def create_user_secret(app_name, username, password):
    v1 = client.CoreV1Api()
    data = {
        "username": username,
        "password": password
    }
    secret = client.V1Secret(metadata=client.V1ObjectMeta(name=f"{app_name}-postgres-secret"),
                             data={k: base64.b64encode(v.encode()).decode() for k, v in data.items()}
                             )
    v1.create_namespaced_secret(namespace="default", body=secret)


def create_config_map(name, data):
    v1 = client.CoreV1Api()
    config_map = client.V1ConfigMap(metadata=client.V1ObjectMeta(name=f"{name}-config"),
                                    data=data
                                    )
    v1.create_namespaced_config_map(namespace="default", body=config_map)


def create_stateful_set(app_name, resources):
    apps_v1 = client.AppsV1Api()

    stateful_set = client.V1StatefulSet(
        api_version="apps/v1",
        kind="StatefulSet",
        metadata=client.V1ObjectMeta(name=app_name),
        spec=client.V1StatefulSetSpec(
            service_name=app_name,
            replicas=1,
            selector=client.V1LabelSelector(match_labels={"app": app_name}),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"app": app_name}),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name=app_name,
                            image="postgres:latest",
                            ports=[client.V1ContainerPort(container_port=5432)],  # Assuming PostgreSQL default port
                            resources=client.V1ResourceRequirements(requests=resources)
                        )
                    ]
                )
            )
        )
    )

    apps_v1.create_namespaced_stateful_set(namespace="default", body=stateful_set)


def create_cronjob_from_yaml():
    with open("cronjob.yaml", "r") as file:
        cronjob_yaml = yaml.safe_load(file)

    batch_v1 = client.BatchV1Api()
    batch_v1.create_namespaced_cron_job(body=cronjob_yaml, namespace="default")


def update_deployment_with_monitor_label(deployment_name):
    v1 = client.AppsV1Api()

    try:
        deployment = v1.read_namespaced_deployment(name=deployment_name, namespace="default")

        labels = deployment.metadata.labels or {}
        labels["monitor"] = "true"
        deployment.metadata.labels = labels

        v1.replace_namespaced_deployment(name=deployment_name, namespace="default", body=deployment)

        return True
    except client.exceptions.ApiException as e:
        print(f"Error updating deployment with monitor label: {str(e)}")
        return False


@app.route('/enablemonitoring', methods=['POST'])
def enable_monitoring():
    app_name = request.json.get('AppName')

    try:
        if update_deployment_with_monitor_label(app_name):
            create_cronjob_from_yaml()
            return jsonify({'message': f'Monitoring enabled for {app_name} and CronJob created'}), 200
        else:
            return jsonify({'error': f'Failed to enable monitoring for {app_name}'}), 500
    except client.exceptions.ApiException as e:
        return jsonify({'error': str(e)}), 500


@app.route('/healthz', methods=['GET'])
def check_health():
    return "OK", 200


@app.route('/health/<app_name>', methods=['GET'])
def get_health_status(app_name):
    v1 = client.CoreV1Api()
   # data = request.json
   # app_name = data['AppName']
    CONFIGMAP_NAME = "health-status"
    NAMESPACE = "default"
    try:
        # Read the ConfigMap
        config_map = v1.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)

        # Retrieve the health status data for the specified app
        status_data = config_map.data.get(app_name)

        if status_data:
            # Convert the status data from JSON string to dictionary
            status_data = json.loads(status_data)
            return jsonify(status_data), 200
        else:
            return jsonify({"error": "App not found"}), 404
    except client.exceptions.ApiException as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5010)
