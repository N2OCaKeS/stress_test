NE_PATH=/home/node_exporter
mkdir $NE_PATH
cd $NE_PATH


cat << EOF > docker-compose.yml
version: '3.7'

services:
  node_exporter:
    image: allta.devos.astralinux.ru:21503/prom/node-exporter:latest
    network_mode: host
#    ports:
#      - "9100:9100"
#    networks:
#      - monitoring

#networks:
#  monitoring:
#    driver: bridge
EOF



#if test "$(grep 1.8 /etc/astra_version)"; then
### С 1.7.10 используется docker-compose-v2
if grep -q '1.8' /etc/astra_version || grep -qE '^(1\.7\.[1-9][0-9]+)$' /etc/astra_version; then
  COMPOSE_CMD="docker compose"
  COMPOSE_VERS="docker-compose-v2"
else
  COMPOSE_CMD="docker-compose"
  COMPOSE_VERS="docker-compose"
fi


sudo tee /etc/systemd/system/node_exporter.service > /dev/null << EOF
[Unit]
Description=Docker node exporter service
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$NE_PATH
ExecStart=/usr/bin/$COMPOSE_CMD up -d
ExecStop=/usr/bin/$COMPOSE_CMD down
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF


sudo apt-get install docker.io -y
sudo apt-get install $COMPOSE_VERS -y

sudo systemctl enable node_exporter.service
sudo systemctl daemon-reload
sudo systemctl start node_exporter.service
sudo systemctl status node_exporter.service
sudo systemctl restart docker.service


