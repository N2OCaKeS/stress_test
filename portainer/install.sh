#!/usr/bin/env bash
set -euo pipefail

# Путь до папки с вашим docker-compose.yml
export COMPOSE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export SERIVE_NAME="portainer.service"

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

generate_shared_tls_cert() {
	local openssl_cfg san_list

	if ! command -v openssl >/dev/null 2>&1; then
		echo "openssl не найден. Установите openssl и повторите команду."
		exit 1
	fi

	sudo mkdir -p "$TLS_CERTS_PATH"

	if sudo test -s "$TLS_CERT_FILE" && sudo test -s "$TLS_KEY_FILE"; then
		return 0
	fi

	openssl_cfg="$(mktemp)"
	san_list="DNS:${ALLTA_EXTERNAL_HOST},DNS:localhost,IP:127.0.0.1"

	cat > "$openssl_cfg" <<EOF
[req]
default_bits = 4096
default_md = sha256
prompt = no
x509_extensions = v3_req
distinguished_name = dn

[dn]
CN = ${ALLTA_EXTERNAL_HOST}

[v3_req]
subjectAltName = ${san_list}
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
basicConstraints = CA:TRUE
EOF

	sudo openssl req \
		-x509 \
		-nodes \
		-newkey rsa:4096 \
		-days "$ALLTA_TLS_DAYS" \
		-keyout "$TLS_KEY_FILE" \
		-out "$TLS_CERT_FILE" \
		-config "$openssl_cfg" >/dev/null 2>&1

	sudo chmod 0600 "$TLS_KEY_FILE"
	sudo chmod 0644 "$TLS_CERT_FILE"
	rm -f "$openssl_cfg"
}

install_shared_tls_cert_to_trust_store() {
	if ! sudo sh -c 'command -v update-ca-certificates >/dev/null 2>&1'; then
		echo "update-ca-certificates не найден, пропускаю установку сертификата в trust store."
		return 0
	fi

	if sudo test -f "$TLS_SYSTEM_CA_FILE" && sudo cmp -s "$TLS_CERT_FILE" "$TLS_SYSTEM_CA_FILE"; then
		return 0
	fi

	sudo install -m 0644 "$TLS_CERT_FILE" "$TLS_SYSTEM_CA_FILE"
	sudo update-ca-certificates >/dev/null
}

prepare_shared_tls() {
	generate_shared_tls_cert
	install_shared_tls_cert_to_trust_store
}

export_allta_ca_build_arg() {
	local cert_b64
	if ! sudo test -s "$TLS_CERT_FILE"; then
		echo "TLS сертификат не найден: $TLS_CERT_FILE"
		exit 1
	fi

	if cert_b64="$(sudo base64 -w 0 "$TLS_CERT_FILE" 2>/dev/null)"; then
		export ALLTA_API_CA_B64="$cert_b64"
	else
		export ALLTA_API_CA_B64="$(sudo base64 "$TLS_CERT_FILE" | tr -d '\n')"
	fi
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
	export FILE_PATH=/var/allta_services
	export BASE_PATH=$FILE_PATH/volumes
	export CRED_PATH=$FILE_PATH/config
	export TLS_CERTS_PATH="$FILE_PATH/certs"
	export TLS_CERT_FILE="$TLS_CERTS_PATH/allta-api.crt"
	export TLS_KEY_FILE="$TLS_CERTS_PATH/allta-api.key"
	export TLS_SYSTEM_CA_FILE="/usr/local/share/ca-certificates/allta-api.crt"
	export ALLTA_TLS_DAYS="${ALLTA_TLS_DAYS:-3650}"
	export ALLTA_EXTERNAL_HOST="allta.devos.astralinux.ru"
	export PORTAINER_PATH=$BASE_PATH/allta_portainer_data
	export OAUTH_CLIENT_SECRETS_PATH=$FILE_PATH/secrets/oauth_clients
}

dir(){
	sudo mkdir -p "$BASE_PATH"
	sudo mkdir -p "$PORTAINER_PATH"
	sudo mkdir -p "$TLS_CERTS_PATH"
	sudo mkdir -p "$OAUTH_CLIENT_SECRETS_PATH"
	sudo mkdir -p "$CRED_PATH"
}

start(){
	exports
	prepare_shared_tls
	export_allta_ca_build_arg
	export DOCKER_BUILDKIT=0
	unset COMPOSE_DOCKER_CLI_BUILD
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
	prepare_shared_tls
	export_allta_ca_build_arg
	export DOCKER_BUILDKIT=0
	unset COMPOSE_DOCKER_CLI_BUILD
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
	sudo apt-get install -y docker-compose docker wget curl openssl ca-certificates
	sudo usermod -aG docker "$USER"
	dir
	prepare_shared_tls
	service
	creds
}

update(){
	exports
	echo "0) ensure shared TLS certificate and trust store"
	prepare_shared_tls
	cd "$COMPOSE_DIR"

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
