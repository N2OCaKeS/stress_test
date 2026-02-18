#!/usr/bin/env bash
set -euo pipefail

# Путь до папки с вашим docker-compose.yml
export COMPOSE_DIR="$(pwd)"
export SERIVE_NAME="portainer.service"

creds(){
	sudo mkdir -p "$CRED_PATH"
	shopt -s nullglob
	for src in "$COMPOSE_DIR/env"/example/*; do
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
Description=Docker Portainer Service
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$COMPOSE_DIR 
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
	# Allow override via environment, but provide sane defaults.
	export FILE_PATH="${FILE_PATH:-/var/allta_services}"
	export BASE_PATH="${BASE_PATH:-$FILE_PATH/volumes}"
	export CRED_PATH="${CRED_PATH:-$FILE_PATH/config}"
	export PORTAINER_PATH="${PORTAINER_PATH:-$BASE_PATH/allta_portainer_data}"
	export OAUTH_CLIENT_SECRETS_PATH="${OAUTH_CLIENT_SECRETS_PATH:-$FILE_PATH/secrets/oauth_clients}"
}

dir(){
	sudo mkdir -p "$BASE_PATH"
	sudo mkdir -p "$PORTAINER_PATH"
	sudo mkdir -p "$OAUTH_CLIENT_SECRETS_PATH"
	sudo mkdir -p "$CRED_PATH"
}

start(){
	exports
	cd "$COMPOSE_DIR"
	if [ ! -f "$CRED_PATH/env.portainer" ]; then
		echo "Missing $CRED_PATH/env.portainer. Run './install.sh precond' and edit the env file first."
		exit 1
	fi
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
	sudo rm -rf /etc/systemd/system/$SERIVE_NAME
	docker-compose --file docker-compose.yml down -v
	sudo systemctl daemon-reexec
    sudo systemctl daemon-reload

	local -a IMAGES=(
		"allta-portainer:latest"
	)
	for img in "${IMAGES[@]}"; do
		echo "Удаляю образ: $img"
		docker image rm "$img" || docker image rm -f "$img" || echo "пропускаю: $img"
	done

	sudo rm -rf $PORTAINER_PATH
}

precond(){
	exports
	sudo apt-get update
	sudo apt-get install -y docker-compose docker wget curl
	sudo usermod -aG docker "$USER"
	dir
	service
	creds
}

# -------- dispatcher ----------
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
