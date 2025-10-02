#!/usr/bin/env bash
set -euo pipefail

# Путь до папки с вашим docker-compose.yml
export INSTALL_PATH="$(pwd)"
export COMPOSE_DIR="$(pwd)/docker_vm"
export SERIVE_NAME="allta_vm.service"

ALLTA_FILE="/etc/nginx/sites-available/allta"
SVC_NAME="allta_vm"
SVC_TARGET="127.0.0.1:21501"

BACKUP_DIR="/etc/nginx/.backups"
BACKUP_PATH=""


UPSTREAM_BLOCK="${UPSTREAM_BLOCK:-$(cat <<EOF
# ${SVC_NAME}
upstream ${SVC_NAME} { server ${SVC_TARGET}; }
# /${SVC_NAME}
EOF
)}"

LOCATIONS_BLOCK="${LOCATIONS_BLOCK:-$(cat <<EOF
    # ${SVC_NAME}

    location ^~ /api/server/ {
        proxy_pass http://allta_vm;
        proxy_set_header Host               \$host;
        proxy_set_header X-Real-IP          \$remote_addr;
        proxy_set_header X-Forwarded-For    \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  \$scheme;
        client_max_body_size 200m;
        proxy_read_timeout  3600s;
        proxy_send_timeout  3600s;
    }

    location ^~ /api/vm/ {
        proxy_pass http://allta_vm;
        proxy_set_header Host               \$host;
        proxy_set_header X-Real-IP          \$remote_addr;
        proxy_set_header X-Forwarded-For    \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  \$scheme;
        client_max_body_size 200m;
        proxy_read_timeout  3600s;
        proxy_send_timeout  3600s;
    }

    location = /service/redis { return 301 /service/redis/; }

    location ^~ /service/redis/ {
        proxy_pass http://allta_vm;
        proxy_set_header Host               \$host;
        proxy_set_header X-Real-IP          \$remote_addr;
        proxy_set_header X-Forwarded-For    \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  \$scheme;
        client_max_body_size 200m;
        proxy_read_timeout  3600s;
        proxy_send_timeout  3600s;
    }

    location = /service/flower { return 301 /service/flower/; }

    location ^~ /service/flower/ {
        proxy_pass http://allta_vm;
        proxy_set_header Host               \$host;
        proxy_set_header X-Real-IP          \$remote_addr;
        proxy_set_header X-Forwarded-For    \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  \$scheme;
        client_max_body_size 200m;
        proxy_read_timeout  3600s;
        proxy_send_timeout  3600s;
    }	

    # /${SVC_NAME}
EOF
)}"

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

nginx_update() {
    if sudo nginx -t; then
        sudo systemctl reload nginx 2>/dev/null || sudo nginx -s reload
        echo "nginx reloaded"
    else
        echo "nginx -t FAILED — restoring: ${BACKUP_PATH}" >&2
        if [[ -n "${BACKUP_PATH}" && -f "${BACKUP_PATH}" ]]; then
            sudo mv -f "${BACKUP_PATH}" "$ALLTA_FILE"
            sudo nginx -t || true
        fi
        exit 1
    fi
}
nginx_add_blocks() {
    BACKUP_PATH="${BACKUP_DIR}/$(basename "$ALLTA_FILE").$(date +%Y%m%d%H%M%S).bak"
    sudo cp -a "$ALLTA_FILE" "$BACKUP_PATH"

    sudo awk -v start="^[[:space:]]*# ${SVC_NAME}[[:space:]]*$" -v stop="^[[:space:]]*# /${SVC_NAME}[[:space:]]*$" '
        BEGIN { skip = 0 }
        {
            if ($0 ~ start) { skip = 1; next }
            if (skip && $0 ~ stop) { skip = 0; next }
            if (!skip) print $0
        }
    ' "$ALLTA_FILE" | sudo tee "${ALLTA_FILE}.wrk" >/dev/null
    sudo mv -f "${ALLTA_FILE}.wrk" "$ALLTA_FILE"

    sudo awk -v blk="$UPSTREAM_BLOCK" '
        BEGIN { n=0; last_up=0; first_srv=0 }
        {
            lines[++n] = $0
            if ($0 ~ /upstream[[:space:]]+[A-Za-z0-9_]+[[:space:]]*\{/) last_up = n
            if (first_srv==0 && $0 ~ /^[[:space:]]*server[[:space:]]*\{/) first_srv = n
        }
        END {
            ins_idx = 0
            if (n == 0) { print blk; exit }  # пустой файл

            if (last_up > 0) {
                # ищем ближайший "# /<тег>" после last_up, но до первого server
                for (i = last_up + 1; i <= n; i++) {
                    if (lines[i] ~ /^[[:space:]]*# \/[A-Za-z0-9_]+[[:space:]]*$/) { ins_idx = i; break }
                    if (first_srv > 0 && i == first_srv) break
                }
                if (ins_idx == 0) ins_idx = last_up
            } else if (first_srv > 0) {
                ins_idx = first_srv - 1
            } else {
                ins_idx = n
            }

            for (i = 1; i <= n; i++) {
                print lines[i]
                if (i == ins_idx) print blk
            }
        }
    ' "$ALLTA_FILE" | sudo tee "${ALLTA_FILE}.wrk" >/dev/null
    sudo mv -f "${ALLTA_FILE}.wrk" "$ALLTA_FILE"

    sudo awk -v blk="$LOCATIONS_BLOCK" '
        BEGIN { n=0; first_srv=0; srvname_line=0 }
        {
            lines[++n] = $0
            if (first_srv==0 && $0 ~ /^[[:space:]]*server[[:space:]]*\{/) first_srv = n
            if (first_srv>0 && srvname_line==0 && $0 ~ /^[[:space:]]*server_name[[:space:]]+/) srvname_line = n
        }
        END {
            if (n == 0) { print blk; exit }  # на всякий случай
            if (srvname_line > 0) {
                for (i=1; i<=n; i++) {
                    print lines[i]
                    if (i == srvname_line) print blk
                }
            } else if (first_srv > 0) {
                for (i=1; i<=n; i++) {
                    print lines[i]
                    if (i == first_srv) print blk
                }
            } else {
                for (i=1; i<=n; i++) print lines[i]
                print blk
            }
        }
    ' "$ALLTA_FILE" | sudo tee "${ALLTA_FILE}.wrk" >/dev/null
    sudo mv -f "${ALLTA_FILE}.wrk" "$ALLTA_FILE"

    nginx_update
}

nginx_remove_blocks() {
    BACKUP_PATH="${BACKUP_DIR}/$(basename "$ALLTA_FILE").$(date +%Y%m%d%H%M%S).bak"
    sudo cp -a "$ALLTA_FILE" "$BACKUP_PATH"

    sudo awk -v start="^[[:space:]]*# ${SVC_NAME}[[:space:]]*$" -v stop="^[[:space:]]*# /${SVC_NAME}[[:space:]]*$" '
        BEGIN { skip=0 }
        {
            if ($0 ~ start) { skip=1; next }
            if (skip && $0 ~ stop) { skip=0; next }
            if (!skip) print $0
        }
    ' "$ALLTA_FILE" | sudo tee "${ALLTA_FILE}.wrk" >/dev/null
    sudo mv -f "${ALLTA_FILE}.wrk" "$ALLTA_FILE"
	nginx_update
}

exports(){
    export FILE_PATH="/var/allta_services"
	export BASE_PATH=$FILE_PATH/volumes
	export CRED_PATH=$FILE_PATH/config
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
		"allta-vm-nginx:latest"
		"allta-server-api:latest"
		"allta-server-db:latest"
		"allta-vm-api:latest"
		"allta-vm-db:latest"
		"allta-vm-celery:latest"
		"allta-vm-flower:latest"
		"allta-redis:latest"
		"allta-redis-commander:latest"
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
	nginx_remove_blocks
}

precond(){
	exports
	sudo apt-get update
	sudo apt-get install -y docker-compose docker wget curl

	sudo usermod -aG docker "$USER"
	sudo systemctl enable docker.service
	sudo systemctl start docker.service
	sudo mkdir -p "$BACKUP_DIR"
	nginx_add_blocks

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
