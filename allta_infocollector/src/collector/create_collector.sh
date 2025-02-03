NE_PATH=/home/node_exporter
mkdir $NE_PATH
cd $NE_PATH


cat << EOF > docker-compose.yml
version: '3.7'

services:
  node_exporter:
    image: prom/node-exporter:latest
    ports:
      - "9100:9100"
    networks:
      - monitoring

networks:
  monitoring:
    driver: bridge
EOF

sudo tee /etc/systemd/system/node_exporter.service > /dev/null << EOF
[Unit]
Description=Docker Compose Node Exporter Service
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$NE_PATH
ExecStart=/usr/bin/docker-compose up -d
ExecStop=/usr/bin/docker-compose down
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF


sudo apt-get install docker.io -y
sudo apt-get install docker-compose -y

sudo systemctl enable node_exporter.service
sudo systemctl daemon-reload
sudo systemctl start node_exporter.service
sudo systemctl status node_exporter.service


