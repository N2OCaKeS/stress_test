#!/bin/bash


sudo apt update
sudo apt-get install -y libsasl2-dev libldap2-dev libkrb5-dev gcc python3-dev libsasl2-modules-gssapi-mit krb5-config krb5-user


python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

