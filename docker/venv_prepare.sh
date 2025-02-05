#!/bin/bash

sudo apt-get install docker.io docker-compose python3-venv -y
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pwd