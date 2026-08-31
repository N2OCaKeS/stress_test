#!/bin/bash

set -vx

# Configure pip before installing or invoking any Python interpreter.
sudo install -d -m 0755 /etc
cat <<'EOF' | sudo tee /etc/pip.conf >/dev/null
[global]
index-url = http://allta.devos.astralinux.ru:3141/root/release
trusted-host = allta.devos.astralinux.ru
EOF
sudo chmod 0644 /etc/pip.conf

PACKAGES=(
  sysstat 
  netcat 
  iperf
)

sudo apt-get update
for pkg in "${PACKAGES[@]}"; do
  echo "Устанавливаем пакет ${pkg}..."
  sudo apt-get install -y "$pkg" || echo "⚠ Предупреждение: не удалось установить ${pkg}"
done

wget ftp://10.177.103.10/allta*.deb
sudo apt-get update
sudo apt-get install -i allta*.deb

sudo apt-get install python3-pip -y

cat /etc/astra/build_version > /home/u/av.txt
uname -r > /home/u/kernel.txt
