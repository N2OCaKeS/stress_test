#!/bin/bash

set -vx

sudo apt update
sudo apt-get install htop -y
sudo apt-get install ssh git resolvconf sysstat -y
sudo timedatectl set-ntp true

test "$(grep 1.7 /etc/astra_version)" && nat_net_name="Wired connection 1"
test "$(grep 1.8 /etc/astra_version)" && nat_net_name="Проводное соединение 1"

vbox_bridge_mask=24
vbox_bridge_gateway=10.177.103.254
iface=`ip a | grep '2: ' | awk '{print$2}' | tr -d ':' | head -n 1`


declare -A virtual_station_17_1_br=( [ip]=10.177.103.101 [domain]=virtual-station-17-1.allta.nt [host]=virtual-station1 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual_station_17_2_br=( [ip]=10.177.103.102 [domain]=virtual-station-17-2.allta.nt [host]=virtual-station2 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual_station_17_3_br=( [ip]=10.177.103.103 [domain]=virtual-station-17-3.allta.nt [host]=virtual-station3 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual_station_17_4_br=( [ip]=10.177.103.104 [domain]=virtual-station-17-4.allta.nt [host]=virtual-station4 [dns]="10.177.180.248, 10.177.128.198" )
declare -A work_station1_br=( [ip]=10.177.103.201 [domain]=work-station1.allta.nt [host]=work-station1 [dns]="10.177.180.248, 10.177.128.198" )
declare -A work_station2_br=( [ip]=10.177.103.202 [domain]=work-station2.allta.nt [host]=work-station2 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual_station_18_1_br=( [ip]=10.177.103.105 [domain]=virtual-station-18-1.allta.nt [host]=virtual-station1 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual_station_18_2_br=( [ip]=10.177.103.106 [domain]=virtual-station-18-2.allta.nt [host]=virtual-station2 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual_station_18_3_br=( [ip]=10.177.103.107 [domain]=virtual-station-18-3.allta.nt [host]=virtual-station3 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual_station_18_4_br=( [ip]=10.177.103.108 [domain]=virtual-station-18-4.allta.nt [host]=virtual-station4 [dns]="10.177.180.248, 10.177.128.198" )


if [ "$1" = "virtual-station-17-1" ]; then
    ip_br=${virtual_station_17_1_br[ip]}
    dns_br="${virtual_station_17_1_br[dns]}"
elif [ "$1" = "virtual-station-17-2" ]; then
    ip_br=${virtual_station_17_2_br[ip]}
    dns_br="${virtual_station_17_2_br[dns]}"
elif [ "$1" = "virtual-station-17-3" ]; then
    ip_br=${virtual_station_17_3_br[ip]}
    dns_br="${virtual_station_17_3_br[dns]}"
elif [ "$1" = "virtual-station-17-4" ]; then
    ip_br=${virtual_station_17_4_br[ip]}
    dns_br="${virtual_station_17_4_br[dns]}"
elif [ "$1" = "work-station1" ]; then
    ip_br=${work_station1_br[ip]}
    dns_br="${work_station1_br[dns]}"
elif [ "$1" = "work-station2" ]; then
    ip_br=${work_station2_br[ip]}
    dns_br="${work_station2_br[dns]}"
elif [ "$1" = "virtual-station-18-1" ]; then
    ip_br=${virtual_station_18_1_br[ip]}
    dns_br="${virtual_station_18_1_br[dns]}"
elif [ "$1" = "virtual-station-18-2" ]; then
    ip_br=${virtual_station_18_2_br[ip]}
    dns_br="${virtual_station_18_2_br[dns]}"
elif [ "$1" = "virtual-station-18-3" ]; then
    ip_br=${virtual_station_18_3_br[ip]}
    dns_br="${virtual_station_18_3_br[dns]}"
elif [ "$1" = "virtual-station-18-4" ]; then
    ip_br=${virtual_station_18_4_br[ip]}
    dns_br="${virtual_station_18_4_br[dns]}"          
fi


cat << EOF > /etc/network/interfaces
source /etc/network/interfaces.d/*

# The loopback network interface
auto lo
iface lo inet loopback

auto $iface
iface $iface inet static
        address $ip_br
        netmask 255.255.255.0
        gateway 10.177.103.254
        dns-nameservers $dns_br
EOF

sudo systemctl restart networking


#sudo nmcli connection modify "${nat_net_name}" ipv4.method manual ip4 $ip_br/$vbox_bridge_mask
#sudo nmcli connection modify "${nat_net_name}" gw4 $vbox_bridge_gateway
#sudo nmcli connection modify "${nat_net_name}" ipv4.dns "$dns_br"
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