#!/bin/bash

dpkg -s jq &> /dev/null || sudo apt-get install jq -y
wget http://allta.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
sudo jq -r ".\"$1\"[]" releases.json > /etc/apt/sources.list
cat << EOF | sudo tee /etc/apt/preferences.d/devel
Package: *
Pin: release l=devel
Pin-Priority: 500

Package: *
Pin: release l=extended
Pin-Priority: 500
EOF
sudo apt update

# TODO ВРЕМЕННОЕ РЕШЕНИЕ, ПОКА НЕ ИСПРАВЛЕНА ОШИБКА С ЗАВИСИМОСТЬЮ ОТ SSHPASS
sudo apt install sshpass -y
sudo apt --fix-broken install -y

sudo apt install python3-pip -y

# create venv in script_dir
#sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev
#python3 -m venv venv

# install python dependencies in venv
#source venv/bin/activate
# Allta devpi package index
sudo python3 -m pip config --global set global.index-url http://allta.devos.astralinux.ru:3141/root/release
sudo python3 -m pip config --global set global.trusted-host allta.devos.astralinux.ru
#pip install --upgrade pip
#pip3 install -r req.txt
