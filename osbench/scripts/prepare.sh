#!/bin/bash

set -e
export CFLAGS="-Wno-error -Wno-stringop-overflow -Wno-stringop-truncation -Wno-unused-result"
export CXXFLAGS="-Wno-error -Wno-stringop-overflow -Wno-stringop-truncation -Wno-unused-result"

PACKAGES=(
  wget 
  build-essential 
  zlib1g-dev
  libncurses5-dev
  libgdbm-dev
  libnss3-dev 
  libssl-dev 
  libreadline-dev 
  libsqlite3-dev  
  libbz2-dev
  libffi-dev 
  strace 
  gcc 
  make 
  libpdp-dev
)


if command -v apt-get &> /dev/null; then
    PM="apt-get"
elif command -v dnf &> /dev/null; then
    PM="dnf"
fi


if [ -z "$PM" ]; then
    echo "Не найден пакетный менеджер"
    exit 1
fi
sudo "$PM" update


for pkg in "${PACKAGES[@]}"; do
  echo "Устанавливаем пакет ${pkg}..."
  sudo "$PM" install -y "$pkg" || echo "⚠️ Предупреждение: не удалось установить ${pkg}"
done


if [[ "$PM" == "apt-get" ]]; then
    wget -nv ftp://10.177.103.10/allta*.deb 2>/dev/null || { echo "❌ Ошибка скачивания"; exit 1; }
    sudo "$PM" install ./allta*.deb
fi


if [ ! -d "/home/u/git" ]; then
    sudo mkdir -p /home/u/git
    sudo chmod -R 777 /home/u/git
fi



sudo mkdir -p /home/u/python
cd /home/u/python || exit 1
sudo wget -nv -P /home/u/python ftp://10.177.103.10/python/* 2>/dev/null || { echo "❌ Ошибка скачивания"; exit 1; }
tar -xf Python-3.12.1.tar.xz || { echo "❌ Ошибка распаковки"; exit 1; }
cd Python-3.12.1 || exit 1
./configure --enable-optimizations 2>&1 | grep -v "warning:" | grep -v "find:" | grep -v "Ошибка 1" || true
make -j "$(nproc)" 2>&1 | grep -v "warning:" | grep -v "find:" || true
sudo make altinstall 2>&1 | grep -v "warning:" || true


python3.12 -m venv venv
source venv/bin/activate
pip install -i http://10.177.103.10:3141/root/release --trusted-host 10.177.103.10:3141 allta==1.2.0 || { echo "⚠️ Ошибка установки allta"; exit 1; }


cd /home/u/git/stress_test/osbench || exit 1
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -r requirements.txt
if [[ $? != 0 ]]; then
    python3.12 -m pip install -r requirements.txt
    if [[ $? != 0 ]]; then
        echo "❌ Ошибка установки зависимостей"
        exit 1
    fi
fi

