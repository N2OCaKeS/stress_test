#!/usr/bin/env bash
set -euo pipefail
export INSTALL_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export COMPOSE_DIR="$INSTALL_PATH/docker_auth"
export SERIVE_NAME="allta_auth.service"

ensure_state_dir() {
	sudo mkdir -p "$STATE_DIR"
}

git_current_branch() {
	git -C "$INSTALL_PATH" symbolic-ref --quiet --short HEAD 2>/dev/null || true
}

write_state_file() {
	local target_file="$1"
	local value="$2"
	ensure_state_dir
	printf '%s\n' "$value" | sudo tee "$target_file" >/dev/null
}

read_state_file() {
	local target_file="$1"
	if ! sudo test -f "$target_file"; then
		return 1
	fi
	sudo cat "$target_file"
}

save_current_git_state() {
	local current_commit current_branch
	current_commit="$(git -C "$INSTALL_PATH" rev-parse HEAD)"
	write_state_file "$PREVIOUS_COMMIT_FILE" "$current_commit"

	current_branch="$(git_current_branch)"
	if [ -n "$current_branch" ]; then
		write_state_file "$PREVIOUS_BRANCH_FILE" "$current_branch"
	fi
}

restore_saved_branch_if_needed() {
	local current_branch saved_branch
	current_branch="$(git_current_branch)"
	if [ -n "$current_branch" ]; then
		return 0
	fi

	saved_branch="$(read_state_file "$PREVIOUS_BRANCH_FILE" 2>/dev/null || true)"
	if [ -z "$saved_branch" ]; then
		echo "HEAD находится в detached state и файл с веткой не найден: $PREVIOUS_BRANCH_FILE"
		echo "Переключитесь на нужную ветку вручную и повторите update."
		exit 1
	fi

	git -C "$INSTALL_PATH" switch "$saved_branch" >/dev/null 2>&1 || git -C "$INSTALL_PATH" checkout "$saved_branch"
}

backup_current_creds() {
	local newname target
	ensure_state_dir
	sudo rm -rf "$CRED_BACKUP_DIR"
	sudo mkdir -p "$CRED_BACKUP_DIR"

	while IFS= read -r newname; do
		target="$CRED_PATH/$newname"
		if sudo test -f "$target"; then
			sudo cp -a -- "$target" "$CRED_BACKUP_DIR/$newname"
		fi
	done < <(list_service_cred_names)
}

remove_service_creds() {
	local newname target
	sudo mkdir -p "$CRED_PATH"

	while IFS= read -r newname; do
		target="$CRED_PATH/$newname"
		sudo rm -f -- "$target"
	done < <(list_service_cred_names)
}

restore_creds_from_backup() {
	local newname backup_file
	if ! sudo test -d "$CRED_BACKUP_DIR"; then
		echo "Бэкап cred не найден: $CRED_BACKUP_DIR"
		exit 1
	fi

	remove_service_creds
	while IFS= read -r newname; do
		backup_file="$CRED_BACKUP_DIR/$newname"
		if sudo test -f "$backup_file"; then
			sudo cp -a -- "$backup_file" "$CRED_PATH/$newname"
		fi
	done < <(list_service_cred_names)
}

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

list_service_cred_names() {
	local src
	shopt -s nullglob
	for src in "$COMPOSE_DIR/env"/example/*; do
		[ -f "$src" ] || continue
		target_env_name_from_example "$src"
	done
	shopt -u nullglob
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
  update       Сохранить pre-update commit, сделать бэкап cred и обновить сервис
  revert       Откатить сервис на commit из файла состояния и восстановить cred
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
    export AUTH_DB_PATH=$BASE_PATH/allta_auth_db_data
    export AUTH_API_DATA_PATH=$BASE_PATH/allta_auth_api_data
    export CONFIG_API_DATA_PATH=$BASE_PATH/allta_config_api_data
    export CONFIG_DB_PATH=$BASE_PATH/allta_config_db_data
    export STATE_DIR="$FILE_PATH/install_state/${SERIVE_NAME%.service}"
    export PREVIOUS_COMMIT_FILE="$STATE_DIR/previous_commit.hash"
    export PREVIOUS_BRANCH_FILE="$STATE_DIR/previous_branch.txt"
    export CRED_BACKUP_DIR="$STATE_DIR/cred_backup"
}

dir(){
    sudo mkdir -p "$FILE_PATH"
    sudo mkdir -p "$BASE_PATH"
    sudo mkdir -p "$AUTH_DB_PATH"
    sudo mkdir -p "$AUTH_API_DATA_PATH"
    sudo mkdir -p "$CONFIG_API_DATA_PATH"
    sudo mkdir -p "$CONFIG_DB_PATH"
    sudo mkdir -p "$REGISTRY_KEYS_PATH"
    sudo mkdir -p "$OAUTH_CLIENT_SECRETS_PATH"
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
        "authservice-auth-nginx:latest"
        "authservice-auth-api:latest"
        "authservice-auth-db:latest"
        "authservice-config-api:latest"
        "authservice-config-db:latest"
        "docs-api-vm-auth:latest"
    )
    
    for img in "${IMAGES[@]}"; do
        echo "Удаляю образ: $img"
        docker image rm "$img" || docker image rm -f "$img" || echo "пропускаю: $img"
    done
    
    sudo rm -rf $AUTH_DB_PATH
    sudo rm -rf $AUTH_API_DATA_PATH
    sudo rm -rf $CONFIG_API_DATA_PATH
    sudo rm -rf $CONFIG_DB_PATH
    sudo rm -rf $REGISTRY_KEYS_PATH
    sudo rm -f $CRED_PATH/env.authservice_auth_api
    sudo rm -f $CRED_PATH/env.authservice_auth_db
    sudo rm -f $CRED_PATH/env.authservice_config_api
    sudo rm -f $CRED_PATH/env.authservice_config_db
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
    # sudo mkdir -p "$BACKUP_DIR"
    dir
    creds
    service
}

update(){
	exports
	cd "$INSTALL_PATH"

	echo "1) save current git state"
	save_current_git_state

	echo "2) backup creds"
	backup_current_creds

	echo "3) restore branch if needed and git pull"
	restore_saved_branch_if_needed
	git pull --ff-only

	echo "4) stop service: $SERIVE_NAME"
	ensure_service_exists
	sudo systemctl stop "$SERIVE_NAME"
    dir

	echo "5) sync creds with example templates"
	update_creds_from_examples

	echo "6) review updated creds"
	review_updated_creds

	echo "7) start service: $SERIVE_NAME"
	sudo systemctl start "$SERIVE_NAME"
}

revert(){
	local target_commit

	exports
	cd "$INSTALL_PATH"
	ensure_service_exists

	target_commit="$(read_state_file "$PREVIOUS_COMMIT_FILE" 2>/dev/null || true)"
	if [ -z "$target_commit" ]; then
		echo "Файл с коммитом для отката не найден или пуст: $PREVIOUS_COMMIT_FILE"
		exit 1
	fi

	if [ -z "$(git_current_branch)" ] && ! sudo test -f "$PREVIOUS_BRANCH_FILE"; then
		echo "Не удалось определить ветку для последующих update. Сначала переключитесь на ветку и выполните update."
		exit 1
	fi

	if [ -n "$(git_current_branch)" ]; then
		write_state_file "$PREVIOUS_BRANCH_FILE" "$(git_current_branch)"
	fi

	echo "1) stop service: $SERIVE_NAME"
	sudo systemctl stop "$SERIVE_NAME"

	echo "2) checkout commit: $target_commit"
	git -C "$INSTALL_PATH" rev-parse --verify "$target_commit^{commit}" >/dev/null
	git -C "$INSTALL_PATH" switch --detach "$target_commit" >/dev/null 2>&1 || git -C "$INSTALL_PATH" checkout --detach "$target_commit"

	echo "3) restore creds from backup"
	restore_creds_from_backup

	echo "4) start service: $SERIVE_NAME"
	sudo systemctl start "$SERIVE_NAME"
}


cmd="${1:-help}"; shift || true
case "$cmd" in
    start)      start "$@";;
    stop)       stop "$@";;
    remove) remove "$@";;
    reinstall) reinstall "$@";;
    update)     update "$@";;
    revert)     revert "$@";;
    precond)    precond "$@";;
    
    help|-h|--help) usage;;
    *) echo "Unknown command: $cmd"; echo; usage; exit 1;;
esac
