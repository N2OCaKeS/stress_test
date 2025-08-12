#!/bin/bash

set -vx

sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install htop -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install ssh git resolvconf sysstat -y
sudo timedatectl set-ntp true

iface=`ip a | grep '2: ' | awk '{print$2}' | tr -d ':' | head -n 1`

ip=$1
dns="10.177.180.248, 10.177.128.198"
gateway="10.177.103.254"

cat << EOF > /etc/network/interfaces
source /etc/network/interfaces.d/*

# The loopback network interface
auto lo
iface lo inet loopback

auto $iface
iface $iface inet static
        address $ip
        netmask 255.255.255.0
        gateway $gateway
        dns-nameservers $dns
EOF

sudo systemctl restart networking
nmcli connection show

echo "u  ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/u
echo "u  ALL=(ALL:ALL) NOPASSWD:ALL" | sudo tee -a /etc/sudoers

ip a
mkdir -p /home/u/git

OUTFILE="/home/u/git/git_clone.py"
touch "$OUTFILE"

cat > "$OUTFILE" <<'EOF'
#!/bin/python3
import subprocess
from os import getcwd
from sys import exit
from ftplib import FTP

def cmd(command):
    subprocess.run([command], shell=True, check=True)


attention_line = '=' * 130
conf_file = getcwd() + '/gitclone.conf'

#Проверяем директорию запуска
if getcwd() != '/home/u/git':
    print('\n', '\033[1m\033[33mВнимание!!!\033[0m')
    print(attention_line)
    print(f'Текущая директория {getcwd()}')
    print('Запустите скрипт из директории /home/u/git')
    print(attention_line, '\n')
    exit(1)

#Удаляем старый гит
try:
    cmd('sudo rm -r /home/u/git/stress_test')
except Exception as e:
    print('\n', '\033[1m\033[33mВнимание!!!\033[0m')
    print(attention_line)
    print(e)
    print(attention_line, '\n')

#Скачиваем конфиг
def download_conf():
    ftp = FTP('10.177.5.111')
    ftp.login()
    ftp.cwd('stress_reports/stress_test_config')
    with open(conf_file, 'wb') as wf:
        ftp.retrbinary('RETR gitclone.conf', wf.write)
    ftp.quit()
    with open(conf_file, 'r') as r:
        conf = r.read()
    return conf

#Клонируем гит
cmd(download_conf())
EOF

chmod +x "$OUTFILE"
echo "Файл $OUTFILE создан и сделан исполняемым."

mkdir /home/u/git/stress_test
cd /home/u/git


# auto eth0
# iface eth0 inet static
#         address 10.177.103.101
#         netmask 255.255.255.0
#         gateway 10.177.103.254
#         dns-nameserver 10.177.128.198

# dns-nameservers 10.177.128.198