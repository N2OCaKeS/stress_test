#!/usr/bin/env bash
set -euo pipefail
export INSTALL_PATH="$(pwd)"
export COMPOSE_DIR="$(pwd)/docker_auth"
export SERIVE_NAME="allta_auth.service"

usage() {
  cat <<'EOF'
Usage: ./manage.sh <command>

Commands:
  start        Запустить docker-compose (build + detach)
  stop         Остановить и убрать контейнеры
  remove	   Удалить все контейнеры и данные
  reinstall	   Переустанавливает сервисы с удалением данных
  precond      Установить зависимости, создать каталоги и скопировать env-файлы в $CRED_PATH
  help         Показать эту справку
EOF
}

service(){
	if ! systemctl is-active --quiet "$SERIVE_NAME"; then
		sudo tee /etc/systemd/system/$SERIVE_NAME > /dev/null << EOF
[Unit]
Description=Docker Allta Auth
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$INSTALL_PATH 
ExecStart=/usr/bin/bash install.sh start
ExecStop=/usr/bin/bash install.sh stop
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF



    sudo systemctl daemon-reexec
    sudo systemctl daemon-reload
    else
        echo "Сервис уже запущен."
    fi
}

exports(){
    export FILE_PATH="/var/allta_services"
	export BASE_PATH=$FILE_PATH/volumes
	export CRED_PATH=$FILE_PATH/config
	export REGISTRY_KEYS_PATH=$FILE_PATH/secrets
	export OAUTH_CLIENT_SECRETS_PATH=$REGISTRY_KEYS_PATH/oauth_clients
	export ALLTA_EXTERNAL_HOST="allta.devos.astralinux.ru"
	export REGISTRY_CERT_SUBJECT="/CN=allta.devos.astralinux.ru"
	export REGISTRY_CERT_DAYS="3650"
	export AUTH_SQL_ECHO="false"
	export CONFIG_API_TOKENS_PATH="/home/u/tokens.json"
	export AUTH_DB_PATH=$BASE_PATH/allta_auth_db_data
	export CONFIG_API_DATA_PATH=$BASE_PATH/allta_config_api_data	
}

dir(){
    sudo mkdir -p "$FILE_PATH"
	sudo mkdir -p "$BASE_PATH"
	sudo mkdir -p "$AUTH_DB_PATH"
	sudo mkdir -p "$CONFIG_API_DATA_PATH"
	sudo mkdir -p "$REGISTRY_KEYS_PATH"
	sudo mkdir -p "$OAUTH_CLIENT_SECRETS_PATH"
}

creds(){
	sudo mkdir -p "$CRED_PATH"	
	cd "$COMPOSE_DIR/env"
	shopt -s nullglob
	for src in example/*; do
		[ -f "$src" ] || continue
		fname=$(basename -- "$src")
		if [[ "$fname" == example.* ]]; then
			newname="${fname#example.}"
		else
			newname="${fname//example/}"
		fi
		sudo cp -n -- "$src" "$CRED_PATH/$newname"
	done
}

start(){
	exports
	cd "$COMPOSE_DIR"
	docker-compose --file docker-compose.yml up --build -d
}

stop(){
	exports
	cd "$COMPOSE_DIR"
	docker-compose --file docker-compose.yml down
}

reinstall(){
	exports
	cd "$COMPOSE_DIR"
	docker-compose --file docker-compose.yml down -v
	sudo rm -rf $BASE_PATH
	dir
	docker-compose --file docker-compose.yml up --build -d
}

remove(){
	exports
	cd "$COMPOSE_DIR"
	sudo systemctl stop $SERIVE_NAME
	docker-compose --file docker-compose.yml down -v
	sudo rm -rf /etc/systemd/system/$SERIVE_NAME
    sudo systemctl daemon-reexec
    sudo systemctl daemon-reload

	local -a IMAGES=(
		"authservice-auth-nginx:latest"
		"authservice-auth-api:latest"
		"authservice-auth-db:latest"
		"authservice-config-api:latest"
        "docs-api-vm-auth:latest"
	)

	for img in "${IMAGES[@]}"; do
		echo "Удаляю образ: $img"
		docker image rm "$img" || docker image rm -f "$img" || echo "пропускаю: $img"
	done

	sudo rm -rf $AUTH_DB_PATH 
    sudo rm -rf $CONFIG_API_DATA_PATH
	sudo rm -rf $REGISTRY_KEYS_PATH
	sudo rm $CRED_PATH/env.allta_auth_api 
	sudo rm $CRED_PATH/env.allta_auth_db 
	sudo rm $CRED_PATH/env.allta_config_api
    sudo rmdir --ignore-fail-on-non-empty $CRED_PATH
    sudo rmdir --ignore-fail-on-non-empty $BASE_PATH
    sudo rmdir --ignore-fail-on-non-empty $FILE_PATH     
}

precond(){
	exports
	# sudo apt-get update
	# sudo apt-get install -y docker-compose docker wget curl
	sudo usermod -aG docker "$USER"
	sudo systemctl enable docker.service
	sudo systemctl start docker.service
	# sudo mkdir -p "$BACKUP_DIR"
	dir
	creds
	service
}


cmd="${1:-help}"; shift || true
case "$cmd" in
  start)      start "$@";;
  stop)       stop "$@";;
  remove) remove "$@";;  
  reinstall) reinstall "$@";;    
  precond)    precond "$@";;

  help|-h|--help) usage;;
  *) echo "Unknown command: $cmd"; echo; usage; exit 1;;
esac
