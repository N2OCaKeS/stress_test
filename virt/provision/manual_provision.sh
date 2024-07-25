#!/bin/bash

set -vx

if [ "$1" == "debian" ] || [ "$1" == "astra" ] || [ "$1" == "alt" ]; then
    pm=apt-get
elif [ "$1" == "rhel" ] || [ "$1" == "redos" ]; then
    pm=yum
fi

sudo $pm update
sudo $pm install -y sysstat
sudo $pm install -y netcat
sudo $pm install -y libffi-dev gcc make libpdp-dev
sudo $pm install -y python3-numpy

