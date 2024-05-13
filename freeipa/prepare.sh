#!/bin/bash

dpkg -s jq &> /dev/null || sudo apt-get install jq -y
wget http://bendiks.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
sudo jq -r ".\"$1\"[]" releases.json > /etc/apt/sources.list
cat << EOF | sudo tee /etc/apt/preferences.d/devel
Package: *
Pin: release l=devel
Pin-Priority: 500
EOF
sudo apt update

# create venv in script_dir
#sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev
#python3 -m venv venv

# install python dependencies in venv
#source venv/bin/activate
#pip install --upgrade pip
#pip3 install -r req.txt