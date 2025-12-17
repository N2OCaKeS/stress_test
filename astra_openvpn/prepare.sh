#!/bin/bash


SYS_VERSION=$(cat /etc/astra/build_version | tr -d '[:space:]')
SYS_KERNEL=$(uname -r | tr -d '[:space:]')

export DEBIAN_FRONTEND=noninteractive
export DEBCONF_NONINTERACTIVE_SEEN=true

mkdir -p results/raw/active
mkdir -p results/raw/iperf
mkdir -p results/raw/openvpn

dpkg -s jq &> /dev/null || sudo apt-get install jq -y
wget http://allta.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
sudo jq -r ".\"$2\"[]" releases.json > /etc/apt/sources.list

if [[ "$SYS_VERSION" == 1.7* ]]; then
    echo "Добавляем строки с extended-repository..."
    grep -E '/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/base-repository' /etc/apt/sources.list | \
    sed 's|/[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+/base-repository|/EXT_latest/extended-repository|' >> /etc/apt/sources.list
fi
echo 1

cat << EOF | sudo tee /etc/apt/preferences.d/devel
Package: *
Pin: release l=devel
Pin-Priority: 500

Package: *
Pin: release l=extended
Pin-Priority: 500
EOF

sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential pkg-config zlib1g-dev libbz2-dev liblzma-dev xz-utils libssl-dev libreadline-dev libsqlite3-dev libffi-dev libncurses5-dev libgdbm-dev libgdbm-compat-dev libnss3-dev libexpat1-dev tk-dev uuid-dev curl wget ca-certificates rustc cargo strace "linux-tools-${SYS_KERNEL}" python3-requests

sudo DEBIAN_FRONTEND=noninteractive apt-get install -y astra-openvpn-server openvpn sshpass iperf iptables tmux

#python
sudo DEBIAN_FRONTEND=noninteractive apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev libffi-dev libncursesw5-dev tk-dev libgdbm-dev libnss3-dev liblzma-dev uuid-dev xz-utils curl wget ca-certificates
sudo mkdir /home/u/python
cd /home/u/python
sudo wget -P /home/u/python ftp://10.177.103.10/python/*
tar -xf Python-3.12.1.tar.xz
cd Python-3.12.1
./configure --enable-optimizations
make -j
sudo make altinstall

python3.12 -m pip install --upgrade pip
python3.12 -m venv venv
cd /home/u/git/stress_test/$1
/home/u/python/Python-3.12.1/venv/bin/python -m pip install -r req.txt
/home/u/python/Python-3.12.1/venv/bin/python -m pip install --upgrade pip
if [[ $? != 0 ]]; then
    /home/u/python/Python-3.12.1/venv/bin/python -m pip install -r req.txt
fi
