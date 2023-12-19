#!/bin/bash

set -vx

sudo apt-get install rsync -y

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
bridge_net_name="Проводное соединение 2"
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

declare -A database1=( [ip]=10.0.0.11 [domain]=database1.balance.rbt [host]=database1 [dns]="10.0.0.10, 8.8.8.8" )
declare -A database2=( [ip]=10.0.0.12 [domain]=database2.balance.rbt [host]=database2 [dns]="10.0.0.10, 8.8.8.8" )
declare -A database3=( [ip]=10.0.0.13 [domain]=database3.balance.rbt [host]=database3 [dns]="10.0.0.10, 8.8.8.8" )
declare -A lbdb1=( [ip]=10.0.0.41 [domain]=lbdb1.balance.rbt [host]=lbdb1 [dns]="10.0.0.10, 8.8.8.8" )
declare -A lbdb2=( [ip]=10.0.0.42 [domain]=lbdb2.balance.rbt [host]=lbdb2 [dns]="10.0.0.10, 8.8.8.8" )
declare -A lbdb3=( [ip]=10.0.0.43 [domain]=lbdb3.balance.rbt [host]=lbdb3 [dns]="10.0.0.10, 8.8.8.8" )
declare -A pgpool=( [ip]=10.0.0.31 [domain]=pgpool.balance.rbt [host]=pgpool [dns]="10.0.0.10, 8.8.8.8" )
declare -A dcfreeipa=( [ip]=10.0.0.10 [domain]=dcfreeipa.balance.rbt [host]=dcfreeipa [dns]="10.0.0.10, 8.8.8.8" )

# declare -A database1_br=( [ip_br]=10.177.103.111 [server-port]=3421 [forward-port]=2021 [mac]=08:00:27:E1:87:C4 [net]=int0) 
# declare -A database2_br=( [ip_br]=10.177.103.112 [server-port]=3422 [forward-port]=2022 [mac]=08:00:27:64:AF:57 [net]=int0)
# declare -A database3_br=( [ip_br]=10.177.103.113 [server-port]=3423 [forward-port]=2025 [mac]=08:00:27:35:FB:4D [net]=int0)
# declare -A lbdb1_br=(     [ip_br]=10.177.103.141 [server-port]=3424 [forward-port]=2024 [mac]=08:00:27:93:D3:2B [net]=int0)
# declare -A lbdb2_br=(     [ip_br]=10.177.103.142 [server-port]=3425 [forward-port]=2023 [mac]=08:00:27:73:E5:1C [net]=int0)
# declare -A lbdb3_br=(     [ip_br]=10.177.103.143 [server-port]=3431 [forward-port]=2026 [mac]=08:00:27:15:29:EA [net]=int0)
# declare -A pgpool_br=(    [ip_br]=10.177.103.131 [server-port]=3432 [forward-port]=2027 [mac]=08:00:27:BF:3D:49 [net]=int0) 
# declare -A dcfreeipa_br=( [ip_br]=10.177.103.110 [server-port]=3434 [forward-port]=2029 [mac]=08:00:27:D3:CB:DD [net]=int0)

# declare -A database1=( [ip]=10.0.0.11 [server-port]=3421 [forward-port]=2021 [mac]=08:00:27:E1:87:C4 [net]=int0) 
# declare -A database2=( [ip]=10.0.0.12 [server-port]=3422 [forward-port]=2022 [mac]=08:00:27:64:AF:57 [net]=int0)
# declare -A database3=( [ip]=10.0.0.13 [server-port]=3423 [forward-port]=2025 [mac]=08:00:27:35:FB:4D [net]=int0)
# declare -A lbdb1=(     [ip]=10.0.0.41 [server-port]=3424 [forward-port]=2024 [mac]=08:00:27:93:D3:2B [net]=int0)
# declare -A lbdb2=(     [ip]=10.0.0.42 [server-port]=3425 [forward-port]=2023 [mac]=08:00:27:73:E5:1C [net]=int0)
# declare -A lbdb3=(     [ip]=10.0.0.43 [server-port]=3431 [forward-port]=2026 [mac]=08:00:27:15:29:EA [net]=int0)
# declare -A pgpool=(    [ip]=10.0.0.31 [server-port]=3432 [forward-port]=2027 [mac]=08:00:27:BF:3D:49 [net]=int0) 
# declare -A dcfreeipa=( [ip]=10.0.0.10 [server-port]=3434 [forward-port]=2029 [mac]=08:00:27:D3:CB:DD [net]=int0)

# sudo systemctl restart networking

#127.0.0.1   localhost localhost.localdomain
cat << EOF > /etc/hosts
127.0.0.1   localhost 
${database1_br[ip]} ${database1_br[domain]} ${database1_br[host]}
${database2_br[ip]} ${database2_br[domain]} ${database2_br[host]}
${database3_br[ip]} ${database3_br[domain]} ${database3_br[host]}
${lbdb1_br[ip]} ${lbdb1_br[domain]} ${lbdb1_br[host]}
${lbdb2_br[ip]} ${lbdb2_br[domain]} ${lbdb2_br[host]}
${lbdb3_br[ip]} ${lbdb3_br[domain]} ${lbdb3_br[host]}
${pgpool_br[ip]} ${pgpool_br[domain]} ${pgpool_br[host]}
${dcfreeipa_br[ip]} ${dcfreeipa_br[domain]} ${dcfreeipa_br[host]}
EOF

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
fi

sudo nmcli connection modify "${nat_net_name}" ipv4.method manual ip4 $ip/$vbox_subnet_mask
sudo nmcli connection modify "${nat_net_name}" gw4 $vbox_gateway
sudo nmcli connection modify "${nat_net_name}" ipv4.dns "$dns"
sudo nmcli connection add type ethernet con-name "${bridge_net_name}" ifname eth1
sudo nmcli connection modify "${bridge_net_name}" ipv4.method manual ip4 $ip_br/$vbox_bridge_mask
sudo nmcli connection modify "${bridge_net_name}" gw4 $vbox_bridge_gateway
sudo nmcli connection modify "${bridge_net_name}" ipv4.dns "$dns_br"
if [ "$1" = "dcfreeipa" ]; then
    sudo ip link set eth0 down
    sudo nmcli con modify "${nat_net_name}" connection.autoconnect no
fi
nmcli connection show

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


