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
sudo dpkg -i allta*.deb

cat /etc/astra/build_version > /home/u/av.txt
uname -r > /home/u/kernel.txt