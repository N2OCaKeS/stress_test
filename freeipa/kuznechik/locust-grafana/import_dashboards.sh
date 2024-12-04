#!/bin/bash

# 10878 14423 14422 15419 - locust
# 13978 - node_exporter

ds=(10878 14423 14422 15419 13978);
for d in "${ds[@]}"; do
  echo -n "Processing $d: "
  j=$(curl -s -k -u "$GRAFANA_CRED" $GRAFANA_HOST/api/gnet/dashboards/$d | jq .json)
  echo "{\"dashboard\":"${j}",\"overwrite\":$GRAFANA_OVERWRITE,\"inputs\":[{\"name\":\"DS_LOCUST\",\"type\":\"datasource\", \"pluginId\":\"postgres\",\"value\":\"$DS_NAME\"}]}" > payload.json
  curl -v -k -u "$GRAFANA_CRED" -H "Accept: application/json"\
    -H "Content-Type: application/json"\
    -d @payload.json \
    $GRAFANA_HOST/api/dashboards/import; echo ""
done

# Add custom graph from file
j=$(jq '.' ./LocustGraph.json)
echo "{\"dashboard\": "${j}",\"overwrite\":$GRAFANA_OVERWRITE,\"inputs\": [{\"name\":\"DS_LOCUST\",\"type\":\"datasource\", \"pluginId\":\"postgres\",\"value\":\"$DS_NAME\"}],\"folderUid\": \"\"}" > payload.json
curl -v -k -u "$GRAFANA_CRED" \
  -H "Accept: application/json"\
  -H "Content-Type: application/json"\
  -d @payload.json \
  $GRAFANA_HOST/api/dashboards/import; echo ""
