#!/bin/bash

18repo() {
cat << EOF > /etc/apt/sources.list
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/installation 1.8_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/extended-repository 1.8_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/devel-repository 1.8_x86-64 main contrib non-free
EOF
}

17repo() {
cat << EOF > /etc/apt/sources.list
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/1.7.1.8/installation/ 1.7_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/1.7.1.8/base-repository/ 1.7_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/1.7.1.8/update-repository/ 1.7_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/EXT_latest/extended-repository/ 1.7_x86-64 main contrib non-free
EOF
}

test "$(grep 1.7 /etc/astra_version)" && 17repo && sudo apt update
test "$(grep 1.8 /etc/astra_version)" && 18repo && sudo apt update

dpkg -s jq &> /dev/null || sudo apt-get install jq -y
wget http://allta.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
if [[ -n $3 ]]; then
    sudo jq -r ".\"$3\"[]" releases.json > /etc/apt/sources.list
else
    sudo jq -r ".\"$2\"[]" releases.json > /etc/apt/sources.list
fi
cat << EOF | sudo tee /etc/apt/preferences.d/devel
Package: *
Pin: release l=devel
Pin-Priority: 500

Package: *
Pin: release l=extended
Pin-Priority: 500
EOF
sudo apt update

# create venv in script_dir
sudo apt install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev
sudo apt-get install -y libffi-dev strace
sudo apt-get install -y python3-requests
if test "$(grep -E '1.8.*' /etc/astra_version)"; then
    sudo apt-get install -y linux-tools-6.1*-generic
    sudo apt-get install -y linux-tools-6.6*-generic
else
    sudo apt-get install -y linux-tools-5.10*-generic linux-tools-5.15*-generic linux-tools-common-5.15*
    sudo apt-get install -y linux-tools-5.15*-lowlatency
    sudo apt-get install -y libssl1.1 psmisc
fi

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

cd /home/u/git/stress_test/$1
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -r req.txt
if [[ $? != 0 ]]; then
    python3.12 -m pip install -r req.txt
fi

sudo mkdir /home/u/modules
sudo wget -P /home/u/modules ftp://10.177.103.10/modules/*
sudo dpkg -i /home/u/modules/*.deb
sudo apt install -fy

if test "$(grep -E '1.8.*' /etc/astra_version)"; then
    sudo mkdir -p /home/u/postgresql_vanilla/16
    sudo wget -P /home/u/postgresql_vanilla/16 ftp://10.177.103.10/postgresql/16/*
else
    sudo mkdir -p /home/u/postgresql_vanilla/11
    sudo wget -P /home/u/postgresql_vanilla/11 ftp://10.177.103.10/postgresql/11/*
fi






# repo()
# {
# sudo echo deb ftp://10.177.5.111/astra/testing/1.8.0.2/devel 1.8_x86-64 main contrib non-free >> /etc/apt/sources.list
# sudo apt update   
# }

# #test "$(grep -E '1.8.*' /etc/astra_version)" && repo


# # create venv in script_dir
# sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev
# sudo apt-get install -y python3-numpy python3-scipy python3-matplotlib python3-lxml python3-bs4 python3-prettytable

# if test "$(grep -E '1.8.*' /etc/astra_version)"; then
#     sudo apt-get install -y linux-tools-6.1*-generic
# else
#     sudo apt-get install -y linux-tools-5.10*-generic linux-tools-5.15*-generic linux-tools-common-5.15*
#     sudo apt-get install -y linux-tools-5.15*-lowlatency
# fi

# #python3-pandas
# #python3 -m venv venv
# sudo mkdir /home/u/modules
# sudo wget -P /home/u/modules ftp://10.177.103.10/modules/*
# sudo dpkg -i /home/u/modules/*.deb
# sudo apt install -fy

# sudo mkdir /home/u/postgresql_vanilla
# sudo wget -P /home/u/postgresql_vanilla ftp://10.177.103.10/postgresql/*

# # install python dependencies in venv
# #source venv/bin/activate
# #pip3 install -r req.txt
# if test "$(grep -E '1.8.*' /etc/astra_version)"; then
#     python3 -m pip install --upgrade pip --break-system-packages
#     python3 -m pip install -r req.txt --break-system-packages
# else
#     python3 -m pip install --upgrade pip
#     python3 -m pip install -r req.txt
# fi
