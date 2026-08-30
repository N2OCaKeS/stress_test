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
sudo python3 -m pip config --global set global.index-url http://allta.devos.astralinux.ru:3141/root/release
sudo python3 -m pip config --global set global.trusted-host allta.devos.astralinux.ru

cat /etc/astra/build_version > /home/u/av.txt
uname -r > /home/u/kernel.txt
