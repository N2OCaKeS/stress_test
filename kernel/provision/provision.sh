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

sudo apt install -y python3-pip
python3 -m pip config set global.extra-index-url https://artifactory.astralinux.ru/artifactory/api/pypi/gca-pypi-remote/simple
sudo python3 -m pip config set global.extra-index-url https://artifactory.astralinux.ru/artifactory/api/pypi/gca-pypi-remote/simple

wget ftp://10.177.103.10/allta_*_amd64.deb
sudo dpkg -i allta_*_amd64.deb

cat /etc/astra/build_version > /home/u/av.txt
uname -r > /home/u/kernel.txt