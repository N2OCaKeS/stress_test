#!/bin/bash


CPATH="/home/u/git/stress_test/astra_openvpn"
SYS_VERSION=$(cat /etc/astra/build_version | tr -d '[:space:]')
SYS_KERNEL=$(uname -r | tr -d '[:space:]')

export DEBIAN_FRONTEND=noninteractive
export DEBCONF_NONINTERACTIVE_SEEN=true

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

# raw results dirs
mkdir -p results
mkdir -p results/raw
mkdir -p results/raw/active
mkdir -p results/raw/iperf
mkdir -p results/raw/openvpn

sudo DEBIAN_FRONTEND=noninteractive apt-get install -y astra-openvpn-server openvpn sshpass iperf iptables tmux


# venv packages
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y   build-essential pkg-config \
  zlib1g-dev libbz2-dev liblzma-dev xz-utils \
  libssl-dev libreadline-dev libsqlite3-dev \
  libffi-dev libncurses5-dev \
  libgdbm-dev libgdbm-compat-dev \
  libnss3-dev libexpat1-dev \
  tk-dev uuid-dev \
  curl wget ca-certificates \
  rustc cargo \
  strace \
  "linux-tools-${SYS_KERNEL}" \
  python3-requests

#python
sudo mkdir /home/u/python
cd /home/u/python
sudo wget -P /home/u/python ftp://10.177.103.10/python/*
tar -xf Python-3.12.1.tar.xz
cd Python-3.12.1
./configure --enable-optimizations
make -j
sudo make altinstall

python3.12 -m venv venv
source venv/bin/activate
cd /home/u/git/stress_test/new_astra_openvpn/
python3.12 -m pip install --upgrade pip
python -m pip install -r req.txt
if [[ $? != 0 ]]; then
    python -m pip install -r req.txt
fi
