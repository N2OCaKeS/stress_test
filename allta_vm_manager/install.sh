#!/usr/bin/env bash
set -e

# Путь до папки с вашим docker-compose.yml
COMPOSE_DIR="/home/u/allta_v3/stress_test/allta_v3/docker_allta"

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


update_devpi(){
	
}