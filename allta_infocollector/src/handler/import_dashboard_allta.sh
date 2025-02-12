#!/bin/bash
set -vx

cd $DB_PATH

export GRAFANA_HOST="${GRAFANA_HOST:=http://$SERVER_IP:3000}"
export GRAFANA_CRED="${GRAFANA_CRED:=admin:admin}"
export GRAFANA_OVERWRITE="${GRAFANA_OVERWRITE:=false}"
export DS_NAME="${DS_NAME:=Prometheus}"
export PROMETHEUS_URL="${PROMETHEUS_URL:=http://prometheus:9090}"


# Создание источника данных
curl -X POST -H "Content-Type: application/json" -u "$GRAFANA_CRED" \
  -d '{
        "name":"'"$DS_NAME"'",
        "type":"prometheus",
        "access":"proxy",
        "url":"'"$PROMETHEUS_URL"'",
        "isDefault":true
      }' \
  "$GRAFANA_HOST/api/datasources"

j=$(jq '.' ./allta_dashboard.json)
echo "{\"dashboard\": ${j},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload2.json

echo waiting...
sleep 5

curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload2.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""  