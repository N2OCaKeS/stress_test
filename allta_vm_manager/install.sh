#!/usr/bin/env bash
set -e

# Путь до папки с вашим docker-compose.yml
COMPOSE_DIR="/home/u/allta_v3/stress_test/allta_v3/docker_allta"

install_dependencies(){
	sudo apt install docker-io docker-compose wget curl
}

create_volume(){
	BASE_PATH="/var/allta_services/volumes"

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

	# Remove all docker containers and volumes
	cd ./docker_allta
	docker-compose --file docker-compose.yml down -v
	cd ..

	# Remove all data directories
	# Front Service
	sudo rm -rf $DEVPI_PATH

	# Back Service
	# Database
	sudo rm -rf $AUTH_DB_PATH
	sudo rm -rf $SERVER_DB_PATH
	sudo rm -rf $VM_DB_PATH

	# Api
	sudo rm -rf $CONFIG_API_DATA_PATH
	sudo rm -rf $VM_API_DATA_PATH
	sudo rm -rf $BASE_PATH

	# Recreate the directories
	sudo mkdir -p $BASE_PATH

	# Front Service
	sudo mkdir -p $DEVPI_PATH

	# Back Service
	# Database
	sudo mkdir -p $AUTH_DB_PATH
	sudo mkdir -p $SERVER_DB_PATH
	sudo mkdir -p $VM_DB_PATH


	# Api
	sudo mkdir -p $CONFIG_API_DATA_PATH
	sudo mkdir -p $SERVER_API_DATA_PATH
	sudo mkdir -p $VM_API_DATA_PATH
}

create_service(){
	
}

docker_up(){
	cd ./docker_allta
	docker-compose --file docker-compose.yml up --build -d 
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
      devpi login root --password=$DEVPI_ADMIN_PASSWORD && \
      devpi user -m root --password=$DEVPI_ADMIN_PASSWORD
      devpi index -y -c release bases=root/pypi mirror_whitelist=\* && \
      devpi user -y -c $DEVPI_USER password=$DEVPI_PASSWORD && \
      devpi login $DEVPI_USER --password $DEVPI_PASSWORD && \
      devpi index -y -c dev bases=root/pypi mirror_whitelist=\*
}

