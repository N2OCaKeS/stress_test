#!/bin/bash

set -vx

kernel="$2"


if [ "$1" = "database1" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install parted astra-freeipa-client -y
    DISK="/dev/vdb"
    sudo parted "$DISK" --script mklabel gpt
    sudo parted "$DISK" --script mkpart primary ext4 1MiB 100%
    sudo mkfs.ext4 "${DISK}1"
    sudo mkdir -p /var/lib/postgresql/ || exit 1
    sudo mount "${DISK}1" /var/lib/postgresql/
    UUID=$(sudo blkid -s UUID -o value "${DISK}1")
    echo "UUID=$UUID /var/lib/postgresql/ ext4 defaults 0 2" | sudo tee -a /etc/fstab || exit 1
    sudo mount -a || exit 1
    echo "Диск ${DISK}1 успешно отформатирован и добавлен в fstab!"
elif [ "$1" = "database2" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install parted astra-freeipa-client -y
    DISK="/dev/vdb"
    sudo parted "$DISK" --script mklabel gpt
    sudo parted "$DISK" --script mkpart primary ext4 1MiB 100%
    sudo mkfs.ext4 "${DISK}1"
    sudo mkdir -p /var/lib/postgresql/ || exit 1
    sudo mount "${DISK}1" /var/lib/postgresql/
    UUID=$(sudo blkid -s UUID -o value "${DISK}1")
    echo "UUID=$UUID /var/lib/postgresql/ ext4 defaults 0 2" | sudo tee -a /etc/fstab || exit 1
    sudo mount -a || exit 1
    echo "Диск ${DISK}1 успешно отформатирован и добавлен в fstab!"
elif [ "$1" = "database3" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install parted astra-freeipa-client -y
    DISK="/dev/vdb"
    sudo parted "$DISK" --script mklabel gpt
    sudo parted "$DISK" --script mkpart primary ext4 1MiB 100%
    sudo mkfs.ext4 "${DISK}1"
    sudo mkdir -p /var/lib/postgresql/ || exit 1
    sudo mount "${DISK}1" /var/lib/postgresql/
    UUID=$(sudo blkid -s UUID -o value "${DISK}1")
    echo "UUID=$UUID /var/lib/postgresql/ ext4 defaults 0 2" | sudo tee -a /etc/fstab || exit 1
    sudo mount -a || exit 1
    echo "Диск ${DISK}1 успешно отформатирован и добавлен в fstab!"    
elif [ "$1" = "lbdb1" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install astra-freeipa-client pgpool2 keepalived postgresql-client sshpass -y 
elif [ "$1" = "lbdb2" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install astra-freeipa-client pgpool2 keepalived postgresql-client sshpass -y 
elif [ "$1" = "lbdb3" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install astra-freeipa-client pgpool2 keepalived postgresql-client sshpass -y 
elif [ "$1" = "dcfreeipa" ]; then   
    sudo DEBIAN_FRONTEND=noninteractive apt-get install astra-freeipa-server -y
fi

sudo timedatectl set-ntp true


# Проверяем, что строка заканчивается на -generic или -lowlatency
if [[ "$kernel" =~ -(generic|lowlatency)$ ]]; then
    suffix="${BASH_REMATCH[1]}"
    
    # Извлекаем первую часть, которая содержит версию с патчем, например "6.6.28"
    version_full=$(echo "$kernel" | cut -d '-' -f1)
    
    # Извлекаем major и minor версии. Для "6.6.28" получаем "6.6"
    version_major_minor=$(echo "$version_full" | awk -F. '{print $1"."$2}')
    
    # Формируем имя пакета. Для примера получим "linux-6.6-generic"
    package_name="linux-${version_major_minor}-${suffix}"
    
    echo "Устанавливаем пакет: $package_name"
    sudo apt-get install "$package_name" -y
else
    echo "Ошибка: параметр ядра '$kernel' не соответствует ожидаемому формату (должен оканчиваться на -generic или -lowlatency)."
    exit 1
fi

sudo apt-get install -y python3-pip
if (grep -q 1.8 /etc/astra_version); then
    python3 -m pip install --upgrade pip --break-system-packages
    python3 -m pip install psycopg2-binary --break-system-packages
else
    python3 -m pip install --upgrade pip
    python3 -m pip install psycopg2-binary
fi
dpkg -s ntpsec &>/dev/null || sudo apt-get install ntpsec -y

if (grep -q 1.7 /etc/astra_version); then
    nat_net_name="Wired connection 1"
elif (grep -q 1.8 /etc/astra_version); then
    nat_net_name="Проводное соединение 1"
fi

dns_br="$3"
dns_search="$4"
sudo nmcli connection modify "${nat_net_name}" ipv4.dns "$dns_br" ipv4.dns-search "$dns_search" ipv4.ignore-auto-dns yes
nmcli connection show
sudo nmcli connection down "${nat_net_name}" && sudo nmcli connection up "${nat_net_name}"
ip a

apt list postgresql* > /home/u/available_packages.txt

kernel="$2"
kernel_conf=$(sudo cat /boot/grub/grub.cfg | grep menuentry_id | awk '{print $17}' | grep $kernel | tr -d "\'")
if ! grep -q '^GRUB_DEFAULT=' /etc/default/grub; then
    echo 'GRUB_DEFAULT=0' | sudo tee -a /etc/default/grub
fi
sudo sed -i "s/GRUB_DEFAULT=.*/GRUB_DEFAULT=$kernel_conf/" /etc/default/grub
sudo update-grub
cat /etc/default/grub | grep GRUB_DEFAULT

# source python/Python-3.12.1/venv/bin/activate && pip uninstall allta -y && pip install -i http://10.177.103.10:3141/debug/debug --trust 10.177.103.10 allta && cd git/stress_test/postgresql/ && git reset --hard HEAD~50 && git pull && python manual_bl_run.py

# source python/Python-3.12.1/venv/bin/activate && pip uninstall allta -y && pip install -i http://10.177.103.10:3141/root/release --trust 10.177.103.10 allta && cd git && python git_clone.py && cd stress_test && git checkout dev_postgresql_balance && cd postgresql && python manual_bl_run.py
