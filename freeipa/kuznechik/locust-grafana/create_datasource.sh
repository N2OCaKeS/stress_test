#!/bin/bash 

# PostgreSQL Timescale Datasource
curl -u "$GRAFANA_CRED" $GRAFANA_HOST/api/datasources -XPOST \
-H "Accept: application/json" \
-H "Content-Type: application/json" \
-d @- << EOF
{
    "access": "proxy",
    "basicAuth": false,
    "basicAuthPassword": "",
    "basicAuthUser": "",
    "database": "postgres",
    "isDefault": false,
    "jsonData": {
        "postgresVersion": 1200,
        "sslmode": "disable",
        "timescaledb": true
    },
    "name": "$DS_NAME",
    "orgId": 1,
    "readOnly": false,
    "secureJsonData": {
        "password": "$PGPASSWORD"
    },
    "type": "postgres",
    "url": "$PGHOST:$PGPORT",
    "user": "postgres",
    "version": 3,
    "withCredentials": false
}
EOF

# Prometheus Datasource
curl -u "$GRAFANA_CRED" $GRAFANA_HOST/api/datasources -XPOST \
-H "Accept: application/json" \
-H "Content-Type: application/json" \
-d @- << EOF
{
    "access": "proxy",
    "type": "prometheus",
    "url": "$PROMETHEUS_URL"
}
EOF

# Alternatively you can use datasource.yml and mount it in container
# 
# apiVersion: 1
# datasources:
#   - name: ${DS_NAME}
#     type: postgres
#     url: ${PGHOST}:${PGPORT}
#     database: postgres
#     access: proxy
#     basicAuth: false
#     basicAuthPassword: ""
#     basicAuthUser: ""
#     version: 3
#     withCredentials: false
#     user: postgres
#     orgId: 1
#     readOnly: false
#     isDefault: false
#     jsonData:
#       postgresVersion: 1200
#       sslmode: disable
#       timescaledb: true
#     secureJsonData:
#       password: ${PGPASSWORD}

#   - name: prometheus
#     access: proxy
#     url: ${PROMETHEUS_URL}
