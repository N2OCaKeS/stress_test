#!/bin/bash

# create venv in script_dir
sudo apt-get install -y virtualenv python3.5-dev python3.5-venv python3-requests python3-pip python3-setuptools libffi-dev
python3 -m virtualenv -p python3 venv
sudo chmod -R 777 venv

# install python dependencies in venv
source venv/bin/activate && python3 libs/setup.py install && pip install --upgrade pip && pip3 install -r req.txt

