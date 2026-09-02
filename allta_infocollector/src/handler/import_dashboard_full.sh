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

j=$(jq '.' ./grafana_dashboard.json)
echo "{\"dashboard\": ${j},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload.json

echo waiting...
sleep 15

# Импорт дэшборда
curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""

j=$(jq '.' ./grafana_dashboard_vscode_dark.json)
echo "{\"dashboard\": ${j},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload_vscode_dark.json

curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload_vscode_dark.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""

j=$(jq '.' ./grafana_dashboard_vscode_light.json)
echo "{\"dashboard\": ${j},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload_vscode_light.json

curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload_vscode_light.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""

j=$(jq '.' ./grafana_dashboard_dark_orange.json)
echo "{\"dashboard\": ${j},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload_dark_orange.json

curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload_dark_orange.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""

j=$(jq '.' ./grafana_dashboard_blue.json)
echo "{\"dashboard\": ${j},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload_blue.json

curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload_blue.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""

echo waiting...
sleep 3

j2=$(jq '.' ./allta_dashboard.json)
echo "{\"dashboard\": ${j2},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload2.json

echo waiting...
sleep 2

curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload2.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""

j2=$(jq '.' ./allta_dashboard_vscode_dark.json)
echo "{\"dashboard\": ${j2},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload2_vscode_dark.json

curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload2_vscode_dark.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""

j2=$(jq '.' ./allta_dashboard_vscode_light.json)
echo "{\"dashboard\": ${j2},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload2_vscode_light.json

curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload2_vscode_light.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""

j2=$(jq '.' ./allta_dashboard_dark_orange.json)
echo "{\"dashboard\": ${j2},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload2_dark_orange.json

curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload2_dark_orange.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""

j2=$(jq '.' ./allta_dashboard_blue.json)
echo "{\"dashboard\": ${j2},\"overwrite\":${GRAFANA_OVERWRITE},\"inputs\": [{\"name\":\"DS_PROMETHEUS\",\"type\":\"datasource\", \"pluginId\":\"prometheus\",\"value\":\"${DS_NAME}\"}],\"folderUid\": \"\"}" > payload2_blue.json

curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @payload2_blue.json \
  "$GRAFANA_HOST/api/dashboards/import"; echo ""
