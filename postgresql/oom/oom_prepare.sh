sudo apt-get install python3-pip -y
# Allta devpi package index
sudo python3 -m pip config --global set global.index-url http://allta.devos.astralinux.ru:3141/root/release
sudo python3 -m pip config --global set global.trusted-host allta.devos.astralinux.ru

python3 -m pip install --upgrade pip
python3 -m pip install requests


