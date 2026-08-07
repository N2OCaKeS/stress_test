#!/bin/bash

set -vx

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
python3 -m pip config set global.extra-index-url https://artifactory.astralinux.ru/artifactory/api/pypi/gca-pypi-remote/simple
sudo python3 -m pip config set global.extra-index-url https://artifactory.astralinux.ru/artifactory/api/pypi/gca-pypi-remote/simple

cat /etc/astra/build_version > /home/u/av.txt
uname -r > /home/u/kernel.txt