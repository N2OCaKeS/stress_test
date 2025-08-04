#! /bin/bash
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

set -a
source ./docker_allta/env/.env.allta_devpi
set +a

cd ./docker_allta
# docker-compose build
docker-compose --file docker-compose.yml up --build -d 

until curl -s -o /dev/null $DEVPI_URL; do
echo "waiting for devpi-server..."
sleep 60
done	

devpi use $DEVPI_URL
devpi login root --password=$DEVPI_ADMIN_PASSWORD
devpi use root/pypi
devpi index -c releases bases=root/pypi mirror_whitelist='*'
