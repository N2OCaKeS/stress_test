#!/bin/bash

sudo usermod -aG docker u
sudo apt-get install docker.io docker-compose python3-venv apache2-utils -y
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pwd