#!/usr/bin/env bash
set -euo pipefail

# Путь до папки с вашим docker-compose.yml
export INSTALL_PATH="$(pwd)"
export COMPOSE_DIR="$(pwd)/docker"
export SERIVE_NAME="docker_registry.service"

usage() {
  cat <<'EOF'
Usage: ./install.sh <command>

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
Description=Docker Registry
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
	export DOCKER_REGISTRY_KEYS_PATH=$FILE_PATH/secrets
	export REGISTRY_KEYS_PATH=$DOCKER_REGISTRY_KEYS_PATH
	export ALLTA_EXTERNAL_HOST="allta.devos.astralinux.ru"

	export DOCKER_REGISTRY_PATH="/home/partimag/docker_registry_data"
	export DOCKER_REGISTRY_CERT=$BASE_PATH/docker_registry_cert
}

dir(){
    sudo mkdir -p "$FILE_PATH"    
	sudo mkdir -p "$BASE_PATH"
	sudo mkdir -p "$CRED_PATH"
	sudo mkdir -p "$DOCKER_REGISTRY_KEYS_PATH"

	sudo mkdir -p "$DOCKER_REGISTRY_PATH"
	sudo mkdir -p "$DOCKER_REGISTRY_CERT"
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
	sudo rm -rf "$DOCKER_REGISTRY_PATH" "$DOCKER_REGISTRY_CERT"
	dir
	# docker-compose --file docker-compose.yml up --build -d
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
		"docker-registry-cert-init:latest"
		"docker-registry:latest"
		"docker-registry-ui:latest"
		"docker-registry-ui-gateway:latest"
	)

	for img in "${IMAGES[@]}"; do
		echo "Удаляю образ: $img"
		docker image rm "$img" || docker image rm -f "$img" || echo "пропускаю: $img"
	done

	sudo rm -rf "$DOCKER_REGISTRY_PATH" "$DOCKER_REGISTRY_CERT"
	sudo rm -f "$CRED_PATH/env.docker_registry"
	sudo rm -f "$CRED_PATH/env.docker_registry_cert_init"
	sudo rm -f "$CRED_PATH/env.docker_registry_ui"

    sudo rmdir --ignore-fail-on-non-empty $CRED_PATH
    sudo rmdir --ignore-fail-on-non-empty $BASE_PATH
    sudo rmdir --ignore-fail-on-non-empty $FILE_PATH    
}

precond(){
	exports
	sudo apt-get update
	sudo apt-get install -y docker-compose docker wget curl
	sudo usermod -aG docker "$USER"
	sudo systemctl enable docker.service
	sudo systemctl start docker.service
	dir
	creds
	service
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
