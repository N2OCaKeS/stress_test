#!/bin/bash

set -vx

18repo() {
cat << EOF > /etc/apt/sources.list
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/installation 1.8_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/extended-repository 1.8_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/devel-repository 1.8_x86-64 main contrib non-free
EOF
}

17repo() {
echo "grub-pc grub-pc/install_devices multiselect /dev/sda" | sudo debconf-set-selections
cat << EOF > /etc/apt/sources.list
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/1.7.1.8/installation/ 1.7_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/1.7.1.8/base-repository/ 1.7_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/1.7.1.8/update-repository/ 1.7_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/EXT_latest/extended-repository/ 1.7_x86-64 main contrib non-free
EOF
}

test "$(grep 1.7 /etc/astra_version)" && 17repo && sudo apt update
test "$(grep 1.8 /etc/astra_version)" && 18repo && sudo apt update
dpkg -s jq &> /dev/null || sudo apt-get install jq -y
wget http://allta.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
sudo jq -r ".\"$3\"[]" releases.json > /etc/apt/sources.list
cat << EOF | sudo tee /etc/apt/preferences.d/devel
Package: *
Pin: release l=devel
Pin-Priority: 500

Package: *
Pin: release l=extended
Pin-Priority: 500
EOF

sudo apt update
sudo astra-update -A -T -r
kernel="$2"
sudo apt-get install $kernel -y
sudo apt-get install htop -y
sudo apt-get install ssh git resolvconf sysstat -y
sudo timedatectl set-ntp true


test "$(grep 1.7 /etc/astra_version)" && nat_net_name="Wired connection 1"
test "$(grep 1.8 /etc/astra_version)" && nat_net_name="Проводное соединение 1"

vbox_bridge_mask=24
vbox_bridge_gateway=10.177.103.254
iface=`ip a | grep '2: ' | awk '{print$2}' | tr -d ':' | head -n 1`


declare -A virtual_station1_br=( [ip]=10.177.103.101 [domain]=virtual-station1.allta.nt [host]=virtual-station1 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual_station2_br=( [ip]=10.177.103.102 [domain]=virtual-station2.allta.nt [host]=virtual-station2 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual_station3_br=( [ip]=10.177.103.103 [domain]=virtual-station3.allta.nt [host]=virtual-station3 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual_station4_br=( [ip]=10.177.103.104 [domain]=virtual-station4.allta.nt [host]=virtual-station4 [dns]="10.177.180.248, 10.177.128.198" )
declare -A work_station1_br=( [ip]=10.177.103.201 [domain]=work-station1.allta.nt [host]=work-station1 [dns]="10.177.180.248, 10.177.128.198" )
declare -A work_station2_br=( [ip]=10.177.103.202 [domain]=work-station2.allta.nt [host]=work-station2 [dns]="10.177.180.248, 10.177.128.198" )



if [ "$1" = "virtual-station1" ]; then
    ip_br=${virtual_station1_br[ip]}
    dns_br="${virtual_station1_br[dns]}"
elif [ "$1" = "virtual-station2" ]; then
    ip_br=${virtual_station2_br[ip]}
    dns_br="${virtual_station2_br[dns]}"
elif [ "$1" = "virtual-station3" ]; then
    ip_br=${virtual_station3_br[ip]}
    dns_br="${virtual_station3_br[dns]}"
elif [ "$1" = "virtual-station4" ]; then
    ip_br=${virtual_station4_br[ip]}
    dns_br="${virtual_station4_br[dns]}"
elif [ "$1" = "work-station1" ]; then
    ip_br=${work_station1_br[ip]}
    dns_br="${work_station1_br[dns]}"
elif [ "$1" = "work-station2" ]; then
    ip_br=${work_station2_br[ip]}
    dns_br="${work_station2_br[dns]}"        
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
kernel="$2"
kernel_conf=$(sudo cat /boot/grub/grub.cfg | grep menuentry_id | awk '{print $17}' | grep $kernel | tr -d "\'")
if ! grep -q '^GRUB_DEFAULT=' /etc/default/grub; then
    echo 'GRUB_DEFAULT=0' | sudo tee -a /etc/default/grub
fi
sudo sed -i "s/GRUB_DEFAULT=.*/GRUB_DEFAULT=$kernel_conf/" /etc/default/grub
sudo update-grub
cat /etc/default/grub | grep GRUB_DEFAULT
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