#!/bin/bash

# create venv in script_dir
sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev
sudo apt-get install -y linux-tools-5.10*-generic linux-tools-5.15*-generic linux-tools-common-5.15*
sudo apt-get install -y linux-tools-5.15*-lowlatency
python3 -m venv venv

# install python dependencies in venv
source venv/bin/activate
pip install --upgrade pip
pip3 install -r req.txt
