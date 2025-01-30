#!/bin/bash

dpkg -s jq &> /dev/null || sudo apt-get install jq -y
wget http://allta.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
sudo jq -r ".\"$2\"[]" releases.json > /etc/apt/sources.list
cat << EOF | sudo tee /etc/apt/preferences.d/devel
Package: *
Pin: release l=devel
Pin-Priority: 500

Package: *
Pin: release l=extended
Pin-Priority: 500
EOF
sudo apt update

# create venv 
sudo apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev
sudo apt-get install -y libffi-dev strace time
sudo apt-get install -y libcurl4-gnutls-dev
sudo apt-get install -y rustc cargo
sudo apt-get install -y python3-requests

if test "$(grep -E '1.8.*' /etc/astra_version)"; then
    sudo apt-get install -y linux-tools-6.1*
    sudo apt-get install -y linux-tools-6.6*
    sudo apt-get install -y linux-tools-6.12*
    sudo apt-get install -y linux-tools-6*
    sudo apt-get install -y linux-tools-common-6.*
else
    sudo apt-get install -y linux-tools-5.10* linux-tools-5.15* linux-tools-common-5.15*
    sudo apt-get install -y linux-tools-5.15*
fi

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

cd /home/u/git/stress_test/$1
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -r req.txt
if [[ $? != 0 ]]; then
    python3.12 -m pip install -r req.txt
fi

