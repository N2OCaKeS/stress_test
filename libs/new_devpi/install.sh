#!/usr/bin/env bash
set -euo pipefail
export INSTALL_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# compose-файл лежит в корне new_devpi, а не в подкаталоге
export COMPOSE_DIR="$INSTALL_PATH"
export SERVICE_NAME="${SERVICE_NAME:-devpi.service}"

# Поддержка обоих вариантов: standalone docker-compose (v1) и плагин docker compose (v2).
DOCKER_COMPOSE_CMD=""
resolve_compose() {
	if [ -n "$DOCKER_COMPOSE_CMD" ]; then
		return 0
	fi
	if command -v docker-compose >/dev/null 2>&1; then
		DOCKER_COMPOSE_CMD="docker-compose"
	elif docker compose version >/dev/null 2>&1; then
		DOCKER_COMPOSE_CMD="docker compose"
	else
		echo "Не найден ни 'docker-compose', ни 'docker compose'. Установите Docker Compose (./install.sh precond)." >&2
		exit 1
	fi
}

compose() {
	resolve_compose
	# без кавычек намеренно: 'docker compose' должен разбиться на два слова
	$DOCKER_COMPOSE_CMD "$@"
}

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
	if ! systemctl cat "$SERVICE_NAME" >/dev/null 2>&1; then
		echo "Сервис '$SERVICE_NAME' не найден. Сначала выполните './install.sh precond'."
		exit 1
	fi
}

open_env_in_editor() {
	# Открываем файл кредов в редакторе, если есть TTY и редактор.
	local file="$1"
	local editor="${EDITOR:-}"

	if [ -z "$editor" ]; then
		if command -v vim >/dev/null 2>&1; then
			editor="vim"
		elif command -v nano >/dev/null 2>&1; then
			editor="nano"
		elif command -v vi >/dev/null 2>&1; then
			editor="vi"
		fi
	fi

	if [ -n "$editor" ] && [ -t 0 ] && [ -t 1 ]; then
		echo "Открываю $file в $editor — впишите реальные значения и сохраните."
		sudo "$editor" "$file"
		return 0
	fi
	return 1
}

ensure_env_file() {
	# env.devpi должен существовать до старта compose. Если его нет — создаём из
	# шаблона, открываем в редакторе для заполнения; без TTY — просим заполнить вручную.
	if sudo test -f "$DEVPI_ENV_FILE"; then
		return 0
	fi
	echo "Файл кредов не найден: $DEVPI_ENV_FILE"
	dir
	creds
	echo "Создан $DEVPI_ENV_FILE из шаблона."
	if open_env_in_editor "$DEVPI_ENV_FILE"; then
		echo "Креды отредактированы, продолжаю запуск."
		return 0
	fi
	echo "Впишите реальные значения (DEVPI_ROOT_PASSWORD, DEVPI_PASSWORD, ALLTA_API_TOKEN) и повторите './install.sh start'."
	exit 1
}

usage() {
  cat <<'EOF'
Usage: ./install.sh <command>

Commands:
  start        Запустить docker-compose (build + detach)
  stop         Остановить и убрать контейнеры
  remove       Удалить контейнер, образ, systemd-unit и креды (данные пакетов
               сохраняются; для удаления данных передайте --purge)
  reinstall    Переустановить сервис с удалением данных пакетов
  update       Сохранить pre-update commit, сделать бэкап cred и обновить сервис
  revert       Откатить сервис на commit из файла состояния и восстановить cred
  precond      Установить зависимости, создать каталоги и скопировать env-файлы в $CRED_PATH
  help         Показать эту справку
EOF
}

service(){
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
		sudo tee /etc/systemd/system/$SERVICE_NAME > /dev/null << EOF
[Unit]
Description=Docker DevPi Service
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
        sudo systemctl enable "$SERVICE_NAME"
        sudo systemctl restart "$SERVICE_NAME"
    else
        echo "Сервис уже запущен."
    fi
}

exports(){
    export FILE_PATH="/var/allta_services"
    export CRED_PATH="$FILE_PATH/config"
    export STATE_DIR="$FILE_PATH/install_state/${SERVICE_NAME%.service}"
    export PREVIOUS_COMMIT_FILE="$STATE_DIR/previous_commit.hash"
    export PREVIOUS_BRANCH_FILE="$STATE_DIR/previous_branch.txt"
    export CRED_BACKUP_DIR="$STATE_DIR/cred_backup"
    export DEVPI_DATA_DIR="/home/partimag/devpi}"
    export DEVPI_ENV_FILE="$CRED_PATH/env.devpi"
}

dir(){
    sudo mkdir -p "$FILE_PATH"
    sudo mkdir -p "$CRED_PATH"
    sudo mkdir -p "$STATE_DIR"
    sudo mkdir -p "$DEVPI_DATA_DIR"
}

creds(){
    sudo mkdir -p "$CRED_PATH"
    shopt -s nullglob
    for src in "$COMPOSE_DIR/env"/example/*; do
        [ -f "$src" ] || continue
        newname="$(target_env_name_from_example "$src")"
        # cp -n: env.devpi создаётся, если его нет, и никогда не перетирается
        sudo cp -n -- "$src" "$CRED_PATH/$newname"
    done
    shopt -u nullglob
}

start(){
    exports
    ensure_env_file
    export DOCKER_BUILDKIT=0
    unset COMPOSE_DOCKER_CLI_BUILD
    # подтягиваем DEVPI_PORT / DEVPI_DATA_DIR для интерполяции в compose
    if sudo test -f "$DEVPI_ENV_FILE"; then
        set -a
        # shellcheck disable=SC1090
        source "$DEVPI_ENV_FILE"
        set +a
    fi
    export DEVPI_ENV_FILE
    export DEVPI_DATA_DIR
    cd "$COMPOSE_DIR"
    compose --file docker-compose.yml up --build -d
}

stop(){
    exports
    cd "$COMPOSE_DIR"
    compose --file docker-compose.yml down
}

reinstall(){
    exports
    ensure_env_file
    export DOCKER_BUILDKIT=0
    unset COMPOSE_DOCKER_CLI_BUILD
    if sudo test -f "$DEVPI_ENV_FILE"; then
        set -a
        # shellcheck disable=SC1090
        source "$DEVPI_ENV_FILE"
        set +a
    fi
    export DEVPI_ENV_FILE
    export DEVPI_DATA_DIR
    cd "$COMPOSE_DIR"
    compose --file docker-compose.yml down -v
    sudo rm -rf "$DEVPI_DATA_DIR"
    dir
    compose --file docker-compose.yml up --build -d
}

remove(){
    local purge=false
    if [ "${1:-}" = "--purge" ]; then
        purge=true
    fi

    exports
    cd "$COMPOSE_DIR"
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    compose --file docker-compose.yml down -v
    sudo rm -rf /etc/systemd/system/$SERVICE_NAME
    sudo systemctl daemon-reexec
    sudo systemctl daemon-reload

    echo "Удаляю образ: new-devpi:latest"
    docker image rm "new-devpi:latest" || docker image rm -f "new-devpi:latest" || echo "пропускаю: new-devpi:latest"

    sudo rm -f "$CRED_PATH/env.devpi"
    sudo rm -rf "$STATE_DIR"

    # Данные пакетов по умолчанию сохраняем — их удаление необратимо.
    if [ "$purge" = true ]; then
        echo "ВНИМАНИЕ: будет удалено хранилище пакетов $DEVPI_DATA_DIR"
        printf 'Подтвердите удаление всех пакетов (yes/NO): '
        read -r answer
        if [ "$answer" = "yes" ]; then
            sudo rm -rf "$DEVPI_DATA_DIR"
            echo "Хранилище пакетов удалено."
        else
            echo "Отмена: хранилище пакетов сохранено."
        fi
    else
        echo "Хранилище пакетов сохранено: $DEVPI_DATA_DIR (для удаления используйте './install.sh remove --purge')"
    fi

    sudo rmdir --ignore-fail-on-non-empty "$CRED_PATH" 2>/dev/null || true
    sudo rmdir --ignore-fail-on-non-empty "$FILE_PATH" 2>/dev/null || true
}

precond(){
    exports
    sudo apt-get update
    sudo apt-get install -y wget curl ca-certificates

    # Docker НЕ трогаем, если уже установлен — иначе ломаем существующий
    # docker-ce/docker.io (конфликт пакетов и плагинов).
    if command -v docker >/dev/null 2>&1; then
        echo "Docker уже установлен ($(docker --version 2>/dev/null)), пропускаю установку."
    else
        sudo apt-get install -y docker.io
    fi

    # Compose ставим, только если недоступны ни плагин v2, ни standalone v1.
    if docker compose version >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1; then
        echo "Docker Compose уже доступен, пропускаю установку."
    else
        sudo apt-get install -y docker-compose-plugin \
            || sudo apt-get install -y docker-compose \
            || echo "Не удалось установить Compose автоматически — поставьте 'docker compose' вручную."
    fi

    sudo usermod -aG docker "$USER" || true
    sudo systemctl enable docker.service >/dev/null 2>&1 || true
    sudo systemctl start docker.service >/dev/null 2>&1 || true
    dir
    creds
    open_env_in_editor "$DEVPI_ENV_FILE" || true
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

	echo "4) stop service: $SERVICE_NAME"
	ensure_service_exists
	sudo systemctl stop "$SERVICE_NAME"
	dir

	echo "5) sync creds with example templates"
	update_creds_from_examples

	echo "6) review updated creds"
	review_updated_creds

	echo "7) start service: $SERVICE_NAME"
	sudo systemctl start "$SERVICE_NAME"
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

	echo "1) stop service: $SERVICE_NAME"
	sudo systemctl stop "$SERVICE_NAME"

	echo "2) checkout commit: $target_commit"
	git -C "$INSTALL_PATH" rev-parse --verify "$target_commit^{commit}" >/dev/null
	git -C "$INSTALL_PATH" switch --detach "$target_commit" >/dev/null 2>&1 || git -C "$INSTALL_PATH" checkout --detach "$target_commit"

	echo "3) restore creds from backup"
	restore_creds_from_backup

	echo "4) start service: $SERVICE_NAME"
	sudo systemctl start "$SERVICE_NAME"
}


cmd="${1:-help}"; shift || true
case "$cmd" in
    start)      start "$@";;
    stop)       stop "$@";;
    remove)     remove "$@";;
    reinstall)  reinstall "$@";;
    update)     update "$@";;
    revert)     revert "$@";;
    precond)    precond "$@";;

    help|-h|--help) usage;;
    *) echo "Unknown command: $cmd"; echo; usage; exit 1;;
esac
