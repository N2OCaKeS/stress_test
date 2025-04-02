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
sudo apt-get install htop -y



nat_net_name="Проводное соединение 1"
vbox_bridge_mask=24
vbox_bridge_gateway=10.177.103.254


declare -A virtual-station1_br=( [ip]=10.177.103.101 [domain]=virtual-station1.allta.nt [host]=virtual-station1 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual-station2_br=( [ip]=10.177.103.102 [domain]=virtual-station2.allta.nt [host]=virtual-station2 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual-station3_br=( [ip]=10.177.103.103 [domain]=virtual-station3.allta.nt [host]=virtual-station3 [dns]="10.177.180.248, 10.177.128.198" )
declare -A virtual-station4_br=( [ip]=10.177.103.104 [domain]=virtual-station4.allta.nt [host]=virtual-station4 [dns]="10.177.180.248, 10.177.128.198" )


if [ "$1" = "virtual-station1" ]; then
    ip_br=${virtual-station1_br[ip]}
    dns_br="${virtual-station1_br[dns]}"
elif [ "$1" = "virtual-station2" ]; then
    ip_br=${virtual-station2_br[ip]}
    dns_br="${virtual-station2_br[dns]}"
elif [ "$1" = "virtual-station3" ]; then
    ip_br=${virtual-station3_br[ip]}
    dns_br="${virtual-station3_br[dns]}"
elif [ "$1" = "virtual-station4" ]; then
    ip_br=${virtual-station4_br[ip]}
    dns_br="${virtual-station4_br[dns]}"
fi


sudo nmcli connection modify "${nat_net_name}" ipv4.method manual ip4 $ip_br/$vbox_bridge_mask
sudo nmcli connection modify "${nat_net_name}" gw4 $vbox_bridge_gateway
sudo nmcli connection modify "${nat_net_name}" ipv4.dns "$dns_br"
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


