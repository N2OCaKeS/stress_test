mkdir -p $PM_DB_PATH
cd $DB_PATH

dpkg -s docker.io || sudo apt-get install docker.io -y
dpkg -s docker-compose || sudo apt-get install docker-compose -y
dpkg -s jq || sudo apt-get install jq -y

sudo tee /etc/systemd/system/grafana_prometheus.service > /dev/null << EOF
[Unit]
Description=Docker Grafana & Prometheus Service
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$DB_PATH
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


