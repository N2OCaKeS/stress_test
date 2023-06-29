#!/bin/bash

# create venv in script_dir
sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev python3-sklearn
python3 -m venv venv

#sudo apt-get install -y python3-dev python3-requests libffi-dev syslog-ng python3-numpy python3-scipy python3-pandas
#sudo apt-get install -y python3-matplotlib
# install python dependencies in venv
source venv/bin/activate
pip install --upgrade pip
pip3 install -r req.txt