import requests
import time
import json
from kubernetes import client, config

# Load Kubernetes configuration
config.load_kube_config()

# Define the namespace and ConfigMap name
NAMESPACE = "default"
CONFIGMAP_NAME = "health-status"

# Define the health check interval (in seconds)
HEALTH_CHECK_INTERVAL = 5

# Define the label selector for pods to monitor
LABEL_SELECTOR = "monitor=true"

# Initialize Kubernetes client
v1 = client.CoreV1Api()


def get_pods_to_monitor():
    """Get the list of pods to monitor based on the label selector."""
    pods = v1.list_namespaced_pod(NAMESPACE, label_selector=LABEL_SELECTOR)
    return [pod.metadata.name for pod in pods.items]


def check_health(pod_name):
    """Check the health of the application running in the pod."""
    url = f"http://{pod_name}/healthz"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            return True
        else:
            return False
    except requests.exceptions.RequestException:
        return False


def update_configmap(status_data):
    """Update the ConfigMap with the health status data."""
    config_map = v1.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    config_map.data = status_data
    v1.replace_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE, config_map)


def main():
    """Main function to monitor the health of the applications."""
    while True:
        pods = get_pods_to_monitor()
        status_data = {}

        for pod in pods:
            health_status = check_health(pod)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

            if health_status:
                status_data[pod] = json.dumps({
                    "status": "healthy",
                    "last_success": timestamp
                })
            else:
                status_data[pod] = json.dumps({
                    "status": "unhealthy",
                    "last_failure": timestamp
                })

        update_configmap(status_data)
        time.sleep(HEALTH_CHECK_INTERVAL)


if __name__ == "__main__":
    main()