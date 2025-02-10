#!/bin/bash

sudo usermod -aG docker u
sudo apt-get install docker.io docker-compose python3-venv apache2-utils pip -y
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install --upgrade setuptools wheel
pip install -r requirements.txt

python3 main.py --test load -ram 100M -t 1 -c 5
