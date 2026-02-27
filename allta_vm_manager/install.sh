#!/usr/bin/env bash
set -euo pipefail

# Путь до папки с вашим docker-compose.yml
export INSTALL_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export COMPOSE_DIR="$INSTALL_PATH/docker_vm"
export SERIVE_NAME="allta_vm.service"

sync_env_from_example() {
	local example_file="$1"
	local target_file="$2"
	local existing_tmp merged_tmp mode

	existing_tmp="$(mktemp)"
	merged_tmp="$(mktemp)"
	mode="644"

	if sudo test -f "$target_file"; then
		sudo cat "$target_file" > "$existing_tmp"
		mode="$(sudo stat -c '%a' "$target_file" 2>/dev/null || echo 644)"
	fi

	awk -v existing_file="$existing_tmp" '
		function strip_cr(s) {
			sub(/\r$/, "", s)
			return s
		}

		function key_of(line, m) {
			if (match(line, /^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=/, m)) {
				return m[1]
			}
			return ""
		}

		BEGIN {
			while ((getline raw < existing_file) > 0) {
				raw = strip_cr(raw)
				key = key_of(raw)
				if (key == "") {
					continue
				}

				value = raw
				sub(/^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=/, "", value)
				values[key] = value

				if (!(key in ordered)) {
					order[++order_len] = key
					ordered[key] = 1
				}
			}
			close(existing_file)
		}

		{
			line = strip_cr($0)
			key = key_of(line)

			if (key != "") {
				rendered[key] = 1
				if (key in values) {
					print key "=" values[key]
				} else {
					print line
				}
				next
			}

			print line
		}

		END {
			extras = 0
			for (i = 1; i <= order_len; i++) {
				key = order[i]
				if (key in rendered) {
					continue
				}

				if (!extras) {
					print ""
					print "# --- Custom parameters preserved from previous file ---"
					extras = 1
				}
				print key "=" values[key]
			}
		}
	' "$example_file" > "$merged_tmp"

	sudo install -m "$mode" "$merged_tmp" "$target_file"
	rm -f "$existing_tmp" "$merged_tmp"
}

target_env_name_from_example() {
	local src="$1"
	local fname newname
	fname=$(basename -- "$src")
	if [[ "$fname" == example.* ]]; then
		newname="${fname#example.}"
	else
		newname="${fname//example/}"
	fi
	printf '%s\n' "$newname"
}

update_creds_from_examples() {
	local src newname target before_tmp after_tmp
	UPDATED_CRED_FILES=()

	shopt -s nullglob
	for src in "$COMPOSE_DIR/env"/example/*; do
		[ -f "$src" ] || continue
		newname="$(target_env_name_from_example "$src")"
		target="$CRED_PATH/$newname"

		before_tmp="$(mktemp)"
		after_tmp="$(mktemp)"
		if sudo test -f "$target"; then
			sudo cat "$target" > "$before_tmp"
		fi

		sync_env_from_example "$src" "$target"
		sudo cat "$target" > "$after_tmp"

		if ! cmp -s "$before_tmp" "$after_tmp"; then
			UPDATED_CRED_FILES+=("$target")
		fi

		rm -f "$before_tmp" "$after_tmp"
	done
	shopt -u nullglob
}

review_updated_creds() {
	local editor_available=true
	local target

	if [ "${#UPDATED_CRED_FILES[@]}" -eq 0 ]; then
		echo "Креды уже актуальны, обновлений не найдено."
		return 0
	fi

	if ! command -v vim >/dev/null 2>&1; then
		editor_available=false
	fi

	if [ ! -t 0 ] || [ ! -t 1 ]; then
		editor_available=false
	fi

	if [ "$editor_available" = true ]; then
		echo "Открываю обновлённые креды в vim:"
		for target in "${UPDATED_CRED_FILES[@]}"; do
			echo " - $target"
			sudo vim "$target"
		done
	else
		echo "Не удалось открыть vim (нет TTY или vim не установлен)."
		echo "Нужно проверить и настроить креды:"
		for target in "${UPDATED_CRED_FILES[@]}"; do
			echo " - $target"
		done
	fi
}

ensure_service_exists() {
	if ! systemctl cat "$SERIVE_NAME" >/dev/null 2>&1; then
		echo "Сервис '$SERIVE_NAME' не найден. Сначала выполните './install.sh precond'."
		exit 1
	fi
}

usage() {
  cat <<'EOF'
Usage: ./manage.sh <command>

Commands:
  start        Запустить docker-compose (build + detach)
  stop         Остановить и убрать контейнеры
  remove	   Удалить все контейнеры и данные
  reinstall	   Переустанавливает сервисы с удалением данных
  update       Обновить git + сервис + креды по шаблонам
  precond      Установить зависимости, создать каталоги и скопировать env-файлы в $CRED_PATH
  help         Показать эту справку
EOF
}

service(){
	if ! systemctl is-active --quiet "$SERIVE_NAME"; then
		sudo tee /etc/systemd/system/$SERIVE_NAME > /dev/null << EOF
[Unit]
Description=Docker Allta Vm
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
	export ALLTA_EXTERNAL_HOST="allta.devos.astralinux.ru"
	export SERVER_DB_PATH=$BASE_PATH/allta_server_db_data
	export VM_DB_PATH=$BASE_PATH/allta_vm_db_data
	export SERVER_API_DATA_PATH=$BASE_PATH/allta_server_api_data
	export VM_API_DATA_PATH=$BASE_PATH/allta_vm_api_data
	export REDIS_PATH=$BASE_PATH/allta_redis_data
}

dir(){
    sudo mkdir -p "$FILE_PATH"    
	sudo mkdir -p "$BASE_PATH"
	sudo mkdir -p "$SERVER_DB_PATH"
	sudo mkdir -p "$VM_DB_PATH"
	sudo mkdir -p "$SERVER_API_DATA_PATH"
	sudo mkdir -p "$VM_API_DATA_PATH"
	sudo mkdir -p "$REDIS_PATH"
}

creds(){
	sudo mkdir -p "$CRED_PATH"	
	shopt -s nullglob
	for src in "$COMPOSE_DIR/env"/example/*; do
		[ -f "$src" ] || continue
		newname="$(target_env_name_from_example "$src")"
		sudo cp -n -- "$src" "$CRED_PATH/$newname"
	done
	shopt -u nullglob
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
		"vmmanager-vm-nginx:latest"
		"vmmanager-server-api:latest"
		"vmmanager-server-db:latest"
		"vmmanager-vm-api:latest"
		"vmmanager-vm-db:latest"
		"vmmanager-vm-celery:latest"
		"vmmanager-vm-flower:latest"
		"vmmanager-redis:latest"
		"vmmanager-redis-commander:latest"
	)

	for img in "${IMAGES[@]}"; do
		echo "Удаляю образ: $img"
		docker image rm "$img" || docker image rm -f "$img" || echo "пропускаю: $img"
	done

	sudo rm -rf $SERVER_DB_PATH $VM_DB_PATH $SERVER_API_DATA_PATH $VM_API_DATA_PATH $REDIS_PATH 
	sudo rm $CRED_PATH/env.allta_redis 
	sudo rm $CRED_PATH/env.allta_redis_commander 
	sudo rm $CRED_PATH/env.allta_server_api 
	sudo rm $CRED_PATH/env.allta_server_db 
	sudo rm $CRED_PATH/env.allta_vm_api 
	sudo rm $CRED_PATH/env.allta_vm_celery 
	sudo rm $CRED_PATH/env.allta_vm_db 
	sudo rm	$CRED_PATH/env.allta_vm_flower
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

update(){
	exports
	cd "$INSTALL_PATH"

	echo "1) git pull"
	git pull --ff-only

	echo "2) stop service: $SERIVE_NAME"
	ensure_service_exists
	sudo systemctl stop "$SERIVE_NAME"

	echo "3) sync creds with example templates"
	update_creds_from_examples

	echo "4) review updated creds"
	review_updated_creds

	echo "5) start service: $SERIVE_NAME"
	sudo systemctl start "$SERIVE_NAME"
}


# -------- dispatcher ----------
cmd="${1:-help}"; shift || true
case "$cmd" in
  start)      start "$@";;
  stop)       stop "$@";;
  remove) remove "$@";;  
  reinstall) reinstall "$@";;    
  update)     update "$@";;
  precond)    precond "$@";;

  help|-h|--help) usage;;
  *) echo "Unknown command: $cmd"; echo; usage; exit 1;;
esac
