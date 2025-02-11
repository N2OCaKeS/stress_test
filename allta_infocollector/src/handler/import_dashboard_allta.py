import os
import json
import time
import requests


DB_PATH = '/home/u/folder_git_for_infocollector/stress_test/allta_infocollector/src/handler'
GRAFANA_HOST = os.getenv('GRAFANA_HOST', 'http://10.177.103.10:3000')
GRAFANA_CRED = os.getenv('GRAFANA_CRED', 'admin:admin')
GRAFANA_OVERWRITE = os.getenv('GRAFANA_OVERWRITE', 'false')
DS_NAME = os.getenv('DS_NAME', 'Prometheus')
PROMETHEUS_URL = os.getenv('PROMETHEUS_URL', 'http://prometheus:9090')


#Create data source
headers = {
    'Content-Type': 'application/json'
}
data_source_payload = {
    "name": DS_NAME,
    "type": "prometheus",
    "access": "proxy",
    "url": PROMETHEUS_URL,
    "isDefault": True
}

response = requests.post(
    f"{GRAFANA_HOST}/api/datasources",
    headers=headers,
    auth=tuple(GRAFANA_CRED.split(':')),
    json=data_source_payload
)

print(response.status_code, response.text)


with open(f'{DB_PATH}/allta_dashboard.json') as f:
    dashboard_json = json.load(f)

dashboard_payload = {
    "dashboard": dashboard_json,
    "overwrite": GRAFANA_OVERWRITE.lower() == 'true',
    "inputs": [{
        "name": "DS_PROMETHEUS",
        "type": "datasource",
        "pluginId": "prometheus",
        "value": DS_NAME
    }],
    "folderUid": ""
}

with open('payload2.json', 'w') as f:
    json.dump(dashboard_payload, f)

print("waiting...")
time.sleep(5)

# Import dashboard
with open('payload2.json') as f:
    dashboard_payload_json = json.load(f)

response = requests.post(
    f"{GRAFANA_HOST}/api/dashboards/import",
    headers=headers,
    auth=tuple(GRAFANA_CRED.split(':')),
    json=dashboard_payload_json
)

print(response.status_code, response.text)