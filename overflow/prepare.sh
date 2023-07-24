#!/bin/bash

sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev
sudo apt-get install -y python3-numpy python3-scipy python3-matplotlib htop

python3 -m pip install --upgrade pip
python3 -m pip install -r req.txt
