#!/bin/bash


CPATH="/home/u/git/stress_test/docker/site/"
SYS_VERSION=$(cat /etc/astra/build_version | tr -d '[:space:]')
SYS_KERNEL=$(uname -r | tr -d '[:space:]')


# set repo
dpkg -s jq &> /dev/null || sudo apt-get install jq -y
wget http://allta.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
sudo jq -r ".\"$2\"[]" releases.json > /etc/apt/sources.list
# доб EXT если версия 1.7
if [[ "$SYS_VERSION" == 1.7* ]]; then
    echo "Добавляем строки с extended-repository..."
    grep -E '/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/base-repository' /etc/apt/sources.list | \
    sed 's|/[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+/base-repository|/EXT_latest/extended-repository|' >> /etc/apt/sources.list
fi

cat << EOF | sudo tee /etc/apt/preferences.d/devel
Package: *
Pin: release l=devel
Pin-Priority: 500

Package: *
Pin: release l=extended
Pin-Priority: 500
EOF

#Hook docker needed restart warning
sudo apt-get update 
for pkg in docker.io docker-compose-v2; do
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y $pkg
    sudo apt-get remove needrestart -y
done

# test packages
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libpq-dev gcc libapache2-mod-wsgi-py3 nginx build-essential zlib1g-dev wget 
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev 
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libbz2-dev libffi-dev strace libcurl4-gnutls-dev  python3-requests liblzma-dev
sudo apt-get install -y linux-tools-`uname -r`


cd /home/u/git/stress_test/*/site/
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
pip install -i http://10.177.103.10:3141/root/release --trust 10.177.103.10 allta
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -r ${CPATH}requirements.txt
if [[ $? != 0 ]]; then
    python3.12 -m pip install -r requirements.txt
fi

sudo mkdir -p /etc/docker
echo '{"debug": true, "astra-sec-level": 6}' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker

