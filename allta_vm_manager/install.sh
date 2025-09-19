#!/usr/bin/env bash
set -euo pipefail

# Путь до папки с вашим docker-compose.yml
export COMPOSE_DIR="/home/u/git_folder_for_allta_service/stress_test/allta_vm_manager/docker_allta"

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

Дополнительно:
  update_devpi Заполнить devpi (использует переменные из env/.env.allta_devpi)
EOF
}

update_devpi(){
	set -a
	source ./docker_allta/env/.env.allta_devpi
	set +a
	until curl -s -o /dev/null http://localhost:3141; do
		echo "waiting for devpi-server..."
		sleep 1
	done
	devpi use "$DEVPI_SERVER_URL" && \
	devpi login root --password="$DEVPI_ADMIN_PASSWORD" && \
	devpi user -m root --password="$DEVPI_ADMIN_PASSWORD"
	devpi index -y -c release bases=root/pypi mirror_whitelist=\* && \
	devpi user -y -c "$DEVPI_USER" password="$DEVPI_PASSWORD" && \
	devpi login "$DEVPI_USER" --password "$DEVPI_PASSWORD" && \
	devpi index -y -c dev bases=root/pypi mirror_whitelist=\*
}

exports(){
	export BASE_PATH="/var/allta_services/volumes"
	export CRED_PATH="/var/allta_services/config"

	# Front Service
	export DEVPI_PATH=$BASE_PATH/allta_devpi_data

	# Back Service
	# Database
	export AUTH_DB_PATH=$BASE_PATH/allta_auth_db_data
	export SERVER_DB_PATH=$BASE_PATH/allta_server_db_data
	export VM_DB_PATH=$BASE_PATH/allta_vm_db_data

	# Api
	export CONFIG_API_DATA_PATH=$BASE_PATH/allta_config_api_data
	export SERVER_API_DATA_PATH=$BASE_PATH/allta_server_api_data
	export VM_API_DATA_PATH=$BASE_PATH/allta_vm_api_data
	export CONFIG_API_TOKENS_PATH=/home/u/tokens.json

	# Redis
	export REDIS_PATH=$BASE_PATH/allta_redis_data

	# Portainer
	export PORTAINER_PATH=$BASE_PATH/allta_portainer_data
}

devpi_export(){
	set -a
	source $CRED_PATH/env.allta_devpi
	set +a	
}

dir(){
	# Create the directories
	sudo mkdir -p "$BASE_PATH"
	# Front Service
	sudo mkdir -p "$DEVPI_PATH"

	# Back Service
	# Database
	sudo mkdir -p "$AUTH_DB_PATH"
	sudo mkdir -p "$SERVER_DB_PATH"
	sudo mkdir -p "$VM_DB_PATH"

	# Api
	sudo mkdir -p "$CONFIG_API_DATA_PATH"
	sudo mkdir -p "$SERVER_API_DATA_PATH"
	sudo mkdir -p "$VM_API_DATA_PATH"

	# REDIS
	sudo mkdir -p "$REDIS_PATH"

	# Portainer
	sudo mkdir -p "$PORTAINER_PATH"
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
	devpi_export
	cd "$COMPOSE_DIR"
	docker-compose --file docker-compose.yml up --build -d
}

stop(){
	exports
	devpi_export
	cd "$COMPOSE_DIR"
	docker-compose --file docker-compose.yml down
}

reinstall(){
	exports
	devpi_export
	cd "$COMPOSE_DIR"
	docker-compose --file docker-compose.yml down -v
	sudo rm -rf $BASE_PATH
	dir
	docker-compose --file docker-compose.yml up --build -d
}

remove(){
	exports
	devpi_export
	cd "$COMPOSE_DIR"
	docker-compose --file docker-compose.yml down -v

	local -a IMAGES=(
		"allta-devpi:latest"
		"allta-nginx:latest"
		"allta-auth-api:latest"
		"allta-auth-db:latest"
		"allta-config-api:latest"
		"allta_server_api:latest"
		"allta-server-db:latest"
		"allta-vm-api:latest"
		"allta-vm-db:latest"
		"allta-vm-celery:latest"
		"allta-vm-flower:latest"
		"allta_swagger:latest"
		"allta-redis:latest"
		"allta-redis-commander:latest"
		"allta-portainer:latest"
	)

	for img in "${IMAGES[@]}"; do
		echo "Удаляю образ: $img"
		docker image rm "$img" || docker image rm -f "$img" || echo "пропускаю: $img"
	done

	sudo rm -rf /var/allta_services
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
}

# -------- dispatcher ----------
cmd="${1:-help}"; shift || true
case "$cmd" in
  start)      start "$@";;
  stop)       stop "$@";;
  remove) remove "$@";;  
  reinstall) reinstall "$@";;    
  precond)    precond "$@";;
  update_devpi) update_devpi "$@";;

  help|-h|--help) usage;;
  *) echo "Unknown command: $cmd"; echo; usage; exit 1;;
esac
