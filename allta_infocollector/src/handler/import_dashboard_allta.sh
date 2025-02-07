#!/bin/bash
set -vx

DB_PATH=/home/u/folder_git_for_infocollector/stress_test/allta_infocollector/src/handler
cd $DB_PATH

export GRAFANA_HOST="${GRAFANA_HOST:=http://10.177.103.10:3000}"
export GRAFANA_CRED="${GRAFANA_CRED:=admin:admin}"
export GRAFANA_OVERWRITE="${GRAFANA_OVERWRITE:=false}"
export DS_NAME="${DS_NAME:=Prometheus}"
export PROMETHEUS_URL="${PROMETHEUS_URL:=http://prometheus:9090}"


j=$(jq '.' ./allta_dashboard.json)
echo "{\"dashboard\": ${j},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload2.json

echo waiting...
sleep 15

curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload2.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""  