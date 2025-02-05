SERVICE_NAME=allta_infocollector.service
S_PATH=/home/u/folder_git_for_infocollector/stress_test/allta_infocollector
DB_PATH=/home/u/folder_git_for_infocollector/stress_test/allta_infocollector/src/handler


install() {
    dpkg -s docker.io || sudo apt-get install docker.io -y
    dpkg -s docker-compose || sudo apt-get install docker-compose -y

    systemctl is-active --quiet grafana_prometheus.service || bash src/handler/prepare_handler.sh

    sudo tee /etc/systemd/system/$SERVICE_NAME > /dev/null << EOF
[Unit]
Description=Docker InfoCollector Service
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$S_PATH
ExecStartPre=/usr/bin/docker-compose pull
ExecStartPre=/usr/bin/docker-compose build
ExecStartPre=/bin/systemctl restart grafana_prometheus.service
ExecStartPre=/bin/sleep 20
ExecStartPre=/bin/bash $DB_PATH/import_dashboard.sh
ExecStart=/usr/bin/docker-compose up -d
ExecStop=/usr/bin/docker-compose down
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl enable $SERVICE_NAME
    sudo systemctl daemon-reload
    sudo systemctl start $SERVICE_NAME
    sudo systemctl status $SERVICE_NAME
}

test ! -f /etc/systemd/system/$SERVICE_NAME && install || echo Сервис уже установлен