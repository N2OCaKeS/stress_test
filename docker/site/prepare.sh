#!/bin/bash

# set repo
VERSION_OS=$(cat /etc/astra/build_version | tr -d '[:space:]')

dpkg -s jq &> /dev/null || sudo apt-get install jq -y
wget http://allta.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
sudo jq -r --arg version "$VERSION_OS" '[.[] | select(.[] | contains($version)) | .[] | select(contains($version))] | unique[]' releases.json > /etc/apt/sources.list

# доб EXT если версия 1.7
if [[ "$VERSION_OS" == 1.7* ]]; then
    echo "Добавляем строки с extended-repository..."
    grep -E '/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/base-repository' /etc/apt/sources.list | \
    sed 's|/[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+/base-repository|/EXT_latest/extended-repository|' >> /etc/apt/sources.list
fi

# test packages
apt-get update
apt-get install -y sudo apt-get install -y gcc 
apt-get install -y libapache2-mod-wsgi-py3 
apt-get install -y docker.io docker-compose 
apt-get install -y nginx 
apt-get install -y postgresql postgresql-contrib 
apt-get install -y redis-server 
apt-get install -y pgbouncer 
apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev 
apt-get install -y libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev 
apt-get install -y libffi-dev strace libcurl4-gnutls-dev rustc cargo python3-requests 
apt-get install -y liblzma-dev

#if test "$(grep -E '1.8.*' /etc/astra_version)"; then
#    sudo apt-get install -y linux-tools-6.1*-generic
#    sudo apt-get install -y linux-tools-common-6.*
#else
#    sudo apt-get install -y linux-tools-5.10*-generic linux-tools-5.15*-generic linux-tools-common-5.15*
#    sudo apt-get install -y linux-tools-5.15*-lowlatency
#fi


sudo mkdir /home/u/python
cd /home/u/python
sudo wget -P /home/u/python ftp://10.177.103.10/python/*
tar -xf Python-3.12.1.tar.xz
cd Python-3.12.1
./configure --enable-optimizations
make -j 6
sudo make altinstall

python3.12 -m venv venv
source venv/bin/activate

cd /home/u/git/stress_test/*/site/
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -r requirements.txt
if [[ $? != 0 ]]; then
    python3.12 -m pip install -r requirements.txt
fi
