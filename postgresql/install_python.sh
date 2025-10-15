set -vx


# create venv in script_dir
sudo apt install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev sshpass
sudo apt-get install -y libffi-dev strace
sudo apt-get install -y python3-requests
sudo apt-get install -y linux-tools-`uname -r`

sudo mkdir /home/python
cd /home/python
sudo wget -P /home/python ftp://10.177.103.10/python/*
tar -xf Python-3.12.1.tar.xz
cd Python-3.12.1
./configure --enable-optimizations
make -j 6
sudo make altinstall

python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -i http://10.177.103.10:3141/root/release --trusted-host 10.177.103.10 allta==1.0.16


