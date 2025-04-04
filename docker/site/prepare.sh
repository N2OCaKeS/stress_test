#!/bin/bash


CPATH="/home/u/git/stress_test/docker/site/"
SYS_VERSION=$(cat /etc/astra/build_version | tr -d '[:space:]')
SYS_KERNEL=$(uname -r | tr -d '[:space:]')


# set repo
dpkg -s jq &> /dev/null || sudo apt-get install jq -y
wget http://allta.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
sudo jq -r --arg version "$SYS_VERSION" '[.[] | select(.[] | contains($version)) | .[] | select(contains($version))] | unique[]' releases.json > /etc/apt/sources.list

# доб EXT если версия 1.7
if [[ "$SYS_VERSION" == 1.7* ]]; then
    echo "Добавляем строки с extended-repository..."
    grep -E '/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/base-repository' /etc/apt/sources.list | \
    sed 's|/[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+/base-repository|/EXT_latest/extended-repository|' >> /etc/apt/sources.list
fi

# test packages
sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libpq-dev gcc libapache2-mod-wsgi-py3 docker.io docker-compose nginx   build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev libffi-dev strace libcurl4-gnutls-dev  python3-requests liblzma-dev

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
