#!/bin/bash

# create venv in script_dir
sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev syslog-ng
sudo apt-get install -y python3-numpy python3-scipy python3-matplotlib python3-lxml python3-pil python3-bs4
#python3-pandas
#python3 -m venv venv
sudo mkdir /home/u/modules
sudo wget -P /home/u/modules ftp://10.177.103.10/modules/*
sudo dpkg -i /home/u/modules/*.deb
sudo apt install -fy

# install python dependencies in venv
#source venv/bin/activate
python3 -m pip install --upgrade pip
pip3 install -r req.txt
