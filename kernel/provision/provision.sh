#!/bin/bash

set -vx

PACKAGES=(
  sysstat 
  netcat
)

sudo apt-get update
for pkg in "${PACKAGES[@]}"; do
  echo "Устанавливаем пакет ${pkg}..."
  sudo apt-get install -y "$pkg" || echo "⚠ Предупреждение: не удалось установить ${pkg}"
done

sudo install -d -m 0755 /etc
printf '%s\n' \
  '[global]' \
  'index-url = http://allta.devos.astralinux.ru:3141/root/release' \
  'trusted-host = allta.devos.astralinux.ru' | sudo tee /etc/pip.conf >/dev/null
sudo chmod 0644 /etc/pip.conf

sudo apt install -y python3-pip

wget ftp://10.177.103.10/allta_*_amd64.deb
sudo dpkg -i allta_*_amd64.deb

cat /etc/astra/build_version > /home/u/av.txt
uname -r > /home/u/kernel.txt
