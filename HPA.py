from kubernetes import client


def setup_hpa_for_main_kaas_web_server():
    hpa = client.AutoscalingV2Api()

    hpa_manifest = {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": {
            "name": "main-kaas-hpa",
            "namespace": "default"
        },
        "spec": {
            "scaleTargetRef": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": "main-kaas-web-server"
            },
            "minReplicas": 2,
            "maxReplicas": 5,
            "metrics": [
                {
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "targetAverageUtilization": 50
                    }
                }
            ]
        }
    }

    try:
        hpa.create_namespaced_horizontal_pod_autoscaler(namespace="default", body=hpa_manifest)
        return True
    except client.exceptions.ApiException as e:
        print(f"Error creating HPA: {str(e)}")
        return False


if __name__ == '__main__':
    if setup_hpa_for_main_kaas_web_server():
        print("HPA for the main KaaS web server is set up successfully.")
    else:
        print("Failed to set up HPA for the main KaaS web server.")
