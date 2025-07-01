#!/usr/bin/env bash
set -e

# Путь до папки с вашим docker-compose.yml
COMPOSE_DIR="/home/u/allta_v3/stress_test/allta_v3/docker_allta"

prepare() {
	echo "Установка docker и docker-compose"
	sudo apt-get update && sudo apt-get install docker.io docker-compose

	echo "Копируем шаблоны"
	cd "$COMPOSE_DIR/env"
	cp -r ./example .

	echo "Переименовываем файлы"
	shopt -s nullglob
	for f in example.*; do
		mv -- "$f" "${f#example.}"
	done

	echo "Создаем каталог для хранения volume"
	mkdir /tmp/allta_services && mkdir /tmp/allta_services/volume
	cd /tmp/allta_services/volume && mkdir allta_devpi_data allta_db_data
}

case "$1" in
prepare)
	echo "Подготовка зависимостей"
	prepare
	echo "Зависимости установлены"
	;;
install)
	echo "Установка ПК Allta"
	install
	echo "Сервисы установлены и запущены"
	# здесь ваш код для «stop»
	;;
delete)
	echo "Удаление ПК Allta"
	# здесь код для «restart»
	;;
*)
	echo "Использование: $0 {prepare|install|delete}"
	exit 1
	;;
esac
