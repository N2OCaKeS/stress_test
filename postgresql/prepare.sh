#!/bin/bash

if [ "$1" = "1.8" ]; then
    sudo echo deb ftp://10.177.5.111/astra/testing/1.8.0.2/devel 1.8_x86-64 main contrib non-free >> /etc/apt/sources.list
    sudo apt update 
fi

# create venv in script_dir
sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev
sudo apt-get install -y python3-numpy python3-scipy python3-matplotlib python3-lxml python3-bs4 python3-prettytable
sudo apt-get install -y linux-tools-5.10*-generic linux-tools-5.15*-generic linux-tools-common-5.15*
sudo apt-get install -y linux-tools-5.15*-lowlatency
#python3-pandas
#python3 -m venv venv
sudo mkdir /home/u/modules
sudo wget -P /home/u/modules ftp://10.177.103.10/modules/*
sudo dpkg -i /home/u/modules/*.deb
sudo apt install -fy

sudo mkdir /home/u/postgresql_vanilla
sudo wget -P /home/u/postgresql_vanilla ftp://10.177.103.10/postgresql/*

# install python dependencies in venv
#source venv/bin/activate
#pip3 install -r req.txt
if [ "$1" = "1.8" ]; then
    python3 -m pip install --upgrade pip --break-system-packages
    python3 -m pip install -r req.txt --break-system-packages
else
    python3 -m pip install --upgrade pip
    python3 -m pip install -r req.txt
fi
