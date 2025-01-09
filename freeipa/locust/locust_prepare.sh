#!/bin/bash


sudo apt update
sudo apt-get install -y libsasl2-dev libldap2-dev libkrb5-dev gcc python3-dev libsasl2-modules-gssapi-mit krb5-config krb5-user python3-pip
sudo apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev
sudo apt-get install -y libffi-dev strace
sudo apt-get install -y python3-requests


sudo mkdir /home/u/python
cd /home/u/python
sudo wget -P /home/u/python ftp://10.177.103.10/python/*
tar -xf Python-3.12.1.tar.xz
cd Python-3.12.1
./configure --enable-optimizations
make -j 6
sudo make altinstall

python3.12 -m venv venv
source venv/bin/activate

cd /home/u/git/stress_test/freeipa
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -r locust/requirements.txt
if [[ $? != 0 ]]; then
    python3.12 -m pip install -r locust/requirements.txt
fi




