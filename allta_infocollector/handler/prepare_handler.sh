G_PATH=/home/grafana_handler
CP=`pwd`
mkdir -p $G_PATH
cd $G_PATH
cp $CP/* $G_PATH

sudo apt-get install docker.io -y
sudo apt-get install docker-compose -y
sudo apt-get install jq -y

sudo tee /etc/systemd/system/grafana_prometheus.service > /dev/null << EOF
[Unit]
Description=Docker Compose Grafana & Prometheus Service
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$G_PATH
ExecStartPre=/usr/bin/docker-compose pull
ExecStart=/usr/bin/docker-compose up -d
ExecStop=/usr/bin/docker-compose down
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable grafana_prometheus.service
sudo systemctl daemon-reload
sudo systemctl start grafana_prometheus.service
sudo systemctl status grafana_prometheus.service

echo waiting...
sleep 15
sudo bash import_dashboard.sh

# http://10.177.5.23:3000/d/rYdddlPWk/node-exporter-full?orgId=1&from=now-5m&to=now&timezone=browser&var-datasource=default&var-job=node_exporter&var-node=10.177.5.32:9100&var-diskdevices=%5Ba-z%5D%2B%7Cnvme%5B0-9%5D%2Bn%5B0-9%5D%2B%7Cmmcblk%5B0-9%5D%2B&refresh=5s&kiosk


