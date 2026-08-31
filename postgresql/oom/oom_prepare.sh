sudo install -d -m 0755 /etc
sudo tee /etc/pip.conf >/dev/null <<'EOF'
[global]
index-url = http://allta.devos.astralinux.ru:3141/root/release
trusted-host = allta.devos.astralinux.ru
EOF
sudo chmod 0644 /etc/pip.conf

sudo apt-get install python3-pip -y
python3 -m pip install --upgrade pip
python3 -m pip install requests

