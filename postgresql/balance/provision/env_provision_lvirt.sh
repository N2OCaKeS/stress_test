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
sudo apt-get install rsync -y
sudo apt-get install htop -y
sudo apt-get install -y gcc make perl
sudo apt-get install linux-[5-6].*-generic -y
sudo apt-get install linux-[5-6].*-lowlatency -y
sudo apt-get install -y python3-pip
python3 -m pip install --upgrade pip
python3 -m pip install psycopg2-binary
dpkg -s ntpsec &> /dev/null || sudo apt-get install ntpsec -y


main_user=u
users63=(root u)
pass=1

# groups for 'u' user
ugroups=(\
  cdrom floppy audio dip video plugdev netdev lpadmin
  scanner astra-console astra-admin
)


vbox_machines=(\
  database1 database2 database3
  lbdb1 lbdb2 lbdb3
  pgpool dcfreeipa
)

nat_net_name="Проводное соединение 1"
bridge_net_name="br0"
vbox_subnet_mask=19
vbox_bridge_mask=24
vbox_gateway=10.0.0.1
vbox_bridge_gateway=10.177.103.254
vbox_nat=QANetwork
vbox_nat_ip=10.0.0.0

declare -A database1_br=( [ip]=10.177.103.111 [domain]=database1.balance.rbt [host]=database1 [dns]="10.177.103.110, 10.177.128.198" )
declare -A database2_br=( [ip]=10.177.103.112 [domain]=database2.balance.rbt [host]=database2 [dns]="10.177.103.110, 10.177.128.198" )
declare -A database3_br=( [ip]=10.177.103.113 [domain]=database3.balance.rbt [host]=database3 [dns]="10.177.103.110, 10.177.128.198" )
declare -A lbdb1_br=( [ip]=10.177.103.141 [domain]=lbdb1.balance.rbt [host]=lbdb1 [dns]="10.177.103.110, 10.177.128.198" )
declare -A lbdb2_br=( [ip]=10.177.103.142 [domain]=lbdb2.balance.rbt [host]=lbdb2 [dns]="10.177.103.110, 10.177.128.198" )
declare -A lbdb3_br=( [ip]=10.177.103.143 [domain]=lbdb3.balance.rbt [host]=lbdb3 [dns]="10.177.103.110, 10.177.128.198" )
declare -A pgpool_br=( [ip]=10.177.103.131 [domain]=pgpool.balance.rbt [host]=pgpool [dns]="10.177.103.110, 10.177.128.198" )
declare -A dcfreeipa_br=( [ip]=10.177.103.110 [domain]=dcfreeipa.balance.rbt [host]=dcfreeipa [dns]="10.177.103.110, 10.177.128.198" )
declare -A test_br=( [ip]=10.177.103.170 [domain]=test.balance.rbt [host]=test [dns]="10.177.103.110, 10.177.128.198" )

declare -A database1=( [ip]=10.0.0.11 [domain]=database1.balance.rbt [host]=database1 [dns]="10.0.0.10, 8.8.8.8" )
declare -A database2=( [ip]=10.0.0.12 [domain]=database2.balance.rbt [host]=database2 [dns]="10.0.0.10, 8.8.8.8" )
declare -A database3=( [ip]=10.0.0.13 [domain]=database3.balance.rbt [host]=database3 [dns]="10.0.0.10, 8.8.8.8" )
declare -A lbdb1=( [ip]=10.0.0.41 [domain]=lbdb1.balance.rbt [host]=lbdb1 [dns]="10.0.0.10, 8.8.8.8" )
declare -A lbdb2=( [ip]=10.0.0.42 [domain]=lbdb2.balance.rbt [host]=lbdb2 [dns]="10.0.0.10, 8.8.8.8" )
declare -A lbdb3=( [ip]=10.0.0.43 [domain]=lbdb3.balance.rbt [host]=lbdb3 [dns]="10.0.0.10, 8.8.8.8" )
declare -A pgpool=( [ip]=10.0.0.31 [domain]=pgpool.balance.rbt [host]=pgpool [dns]="10.0.0.10, 8.8.8.8" )
declare -A dcfreeipa=( [ip]=10.0.0.10 [domain]=dcfreeipa.balance.rbt [host]=dcfreeipa [dns]="10.0.0.10, 8.8.8.8" )
declare -A test=( [ip]=10.0.0.10 [domain]=test.balance.rbt [host]=test [dns]="10.0.0.10, 8.8.8.8" )


#127.0.0.1   localhost localhost.localdomain
cat << EOF > /etc/hosts
10.177.5.111    qa111.devos.astralinux.ru
127.0.0.1   localhost 
${database1_br[ip]} ${database1_br[domain]} ${database1_br[host]}
${database2_br[ip]} ${database2_br[domain]} ${database2_br[host]}
${database3_br[ip]} ${database3_br[domain]} ${database3_br[host]}
${lbdb1_br[ip]} ${lbdb1_br[domain]} ${lbdb1_br[host]}
${lbdb2_br[ip]} ${lbdb2_br[domain]} ${lbdb2_br[host]}
${lbdb3_br[ip]} ${lbdb3_br[domain]} ${lbdb3_br[host]}
${dcfreeipa_br[ip]} ${dcfreeipa_br[domain]} ${dcfreeipa_br[host]}
EOF
#${pgpool_br[ip]} ${pgpool_br[domain]} ${pgpool_br[host]}

if [ "$1" = "dcfreeipa" ]; then
    echo 127.0.0.1   localhost.localdomain >> /etc/hosts
fi

if [ "$1" = "database1" ]; then
    ip_br=${database1_br[ip]}
    dns_br="${database1_br[dns]}"
    ip=${database1[ip]}
    dns="${database1[dns]}"
elif [ "$1" = "database2" ]; then
    ip_br=${database2_br[ip]}
    dns_br="${database2_br[dns]}"
    ip=${database2[ip]}
    dns="${database2[dns]}"
elif [ "$1" = "database3" ]; then
    ip_br=${database3_br[ip]}
    dns_br="${database3_br[dns]}"
    ip=${database3[ip]}
    dns="${database3[dns]}"
elif [ "$1" = "lbdb1" ]; then
    ip_br=${lbdb1_br[ip]}
    dns_br="${lbdb1_br[dns]}"
    ip=${lbdb1[ip]}
    dns="${lbdb1[dns]}"
elif [ "$1" = "lbdb2" ]; then
    ip_br=${lbdb2_br[ip]}
    dns_br="${lbdb2_br[dns]}"
    ip=${lbdb2[ip]}
    dns="${lbdb2[dns]}"
elif [ "$1" = "lbdb3" ]; then
    ip_br=${lbdb3_br[ip]}
    dns_br="${lbdb3_br[dns]}"
    ip=${lbdb3[ip]}
    dns="${lbdb3[dns]}"
elif [ "$1" = "pgpool" ]; then
    ip_br=${pgpool_br[ip]}
    dns_br="${pgpool_br[dns]}"
    ip=${pgpool[ip]}
    dns="${pgpool[dns]}"
elif [ "$1" = "dcfreeipa" ]; then
    ip_br=${dcfreeipa_br[ip]}
    dns_br="${dcfreeipa_br[dns]}"
    ip=${dcfreeipa[ip]}
    dns="${dcfreeipa[dns]}"
elif [ "$1" = "test" ]; then
    ip_br=${test_br[ip]}
    dns_br="${test_br[dns]}"
    ip=${test[ip]}
    dns="${test[dns]}"
fi


#sudo nmcli connection modify "${nat_net_name}" ipv4.method manual ip4 $ip_br/$vbox_bridge_mask
#sudo nmcli connection modify "${nat_net_name}" gw4 $vbox_bridge_gateway
#sudo nmcli connection modify "${nat_net_name}" ipv4.dns "$dns_br"
#sudo nmcli connection modify "${nat_net_name}" 802-3-ethernet.mac-address $mac_br

IFACE=`ip -o link show | awk -F': ' '{print $2}' | head -n 2 | tail -n 1`
cat << EOF > /etc/network/interfaces

source /etc/network/interfaces.d/*

# The loopback network interface
auto lo
iface lo inet loopback

auto $IFACE
iface $IFACE inet static
    address $ip_br
    netmask 255.255.255.0
    gateway $vbox_bridge_gateway
    dns-nameserver $dns_br

EOF

cat /etc/network/interfaces
sudo systemctl restart networking
nmcli connection show
ip a


if id "$main_user" >/dev/null 2>&1; then
    echo "$main_user:$pass" | chpasswd 2>/dev/null
    chfn -f "" "$main_user"
    for group in ${ugroups[*]}; do usermod -aG $group $main_user; done
else
    useradd -m $main_user -s /bin/bash && echo "$main_user:$pass" | chpasswd 2>/dev/null
    for group in ${ugroups[*]}; do usermod -aG $group $main_user; done
fi

sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
echo "root:$pass" | chpasswd 2>/dev/null

for user in ${users63[*]}; do
    pdpl-user $user -i 63
done

echo "u  ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/u
echo "u  ALL=(ALL:ALL) NOPASSWD:ALL" | sudo tee -a /etc/sudoers
echo "postgres  ALL=(ALL:ALL) NOPASSWD:ALL" | sudo tee -a /etc/sudoers
ls -l /etc/sudoers.d/
cat /etc/sudoers.d/u
cat /etc/sudoers

apt list postgresql* > /home/u/available_packages.txt


kernel="$2"
kernel_conf=$(sudo cat /boot/grub/grub.cfg | grep menuentry_id | awk '{print $17}' | grep $kernel | tr -d "\'")
if ! grep -q '^GRUB_DEFAULT=' /etc/default/grub; then
    echo 'GRUB_DEFAULT=0' | sudo tee -a /etc/default/grub
fi
sudo sed -i "s/GRUB_DEFAULT=.*/GRUB_DEFAULT=$kernel_conf/" /etc/default/grub
sudo update-grub
cat /etc/default/grub | grep GRUB_DEFAULT

