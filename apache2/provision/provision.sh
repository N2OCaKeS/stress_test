#!/bin/bash

set -vx

PACKAGES=(
  apache2 
  libapache2-mod-authnz-pam
  apache2-utils
  curl
)

sudo apt-get update
for pkg in "${PACKAGES[@]}"; do
  echo "Устанавливаем пакет ${pkg}..."
  sudo apt-get install -y "$pkg" || echo "⚠ Предупреждение: не удалось установить ${pkg}"
done

sudo apt install python3-pip -y
sudo python3 -m pip config --global set global.index-url http://allta.devos.astralinux.ru:3141/root/release
sudo python3 -m pip config --global set global.trusted-host allta.devos.astralinux.ru

wget ftp://10.177.103.10/allta*.deb
sudo apt-get install ./allta*.deb -y

cat /etc/astra/build_version > /home/u/av.txt
uname -r > /home/u/kernel.txt
