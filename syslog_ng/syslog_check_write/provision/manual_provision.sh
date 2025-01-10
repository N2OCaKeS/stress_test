#!/bin/bash

set -vx

if [ "$1" == "debian" ] || [ "$1" == "astra" ] || [ "$1" == "alt" ]; then
    pm=apt-get
elif [ "$1" == "rhel" ] || [ "$1" == "redos" ]; then
    pm=yum
fi

if [ "$1" == "astra" ]; then
    18repo() {
    cat << EOF > /etc/apt/sources.list
    deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/installation 1.8_x86-64 main contrib non-free
    deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/extended-repository 1.8_x86-64 main contrib non-free
    deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/devel-repository 1.8_x86-64 main contrib non-free
EOF
    }

    test "$(grep 1.8 /etc/astra_version)" && 18repo && sudo apt update
    dpkg -s jq &> /dev/null || sudo apt-get install jq -y
    wget http://allta.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
    sudo jq -r ".\"$2\"[]" releases.json > /etc/apt/sources.list
    cat << EOF | sudo tee /etc/apt/preferences.d/devel
    Package: *
    Pin: release l=devel
    Pin-Priority: 500

    Package: *
    Pin: release l=extended
    Pin-Priority: 500
EOF
    sudo $pm update
    sudo astra-update -A -T -r
fi

sudo $pm update
sudo $pm install -y sysstat
sudo $pm install -y netcat
sudo $pm install -y libffi-dev gcc make libpdp-dev
sudo $pm install -y python3-numpy