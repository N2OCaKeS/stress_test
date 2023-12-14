#!/bin/bash

main_user=u
pass=1

# groups for 'u' user
ugroups=(\
  cdrom floppy audio dip video plugdev netdev lpadmin
  scanner astra-console astra-admin
)

# database1="192.168.60.110"
# database2="192.168.60.111"
# database3="192.168.60.112"
# lbdb1="192.168.60.120"
# lbdb2="192.168.60.121"
# lbdb3="192.168.60.122"
# pgpool="192.168.60.130"
# dcfreeipa="192.168.60.100"


net_name="Проводное соединение 1"
vbox_subnet_mask=19
vbox_gateway=10.0.0.1

declare -A database1=( [ip]=10.0.10.11 [domain]=database1.balance.rbt [host]=database1 [dns]="10.0.0.21, 8.8.8.8" )
declare -A database2=( [ip]=10.0.10.12 [domain]=database2.balance.rbt [host]=database2 [dns]="10.0.0.21, 8.8.8.8" )
declare -A database3=( [ip]=10.0.10.13 [domain]=database3.balance.rbt [host]=database3 [dns]="10.0.0.21, 8.8.8.8" )
declare -A lbdb1=( [ip]=10.0.10.21 [domain]=lbdb1.balance.rbt [host]=lbdb1 [dns]="10.0.0.21, 8.8.8.8" )
declare -A lbdb2=( [ip]=10.0.10.21 [domain]=lbdb2.balance.rbt [host]=lbdb2 [dns]="10.0.0.21, 8.8.8.8" )
declare -A lbdb3=( [ip]=10.0.10.21 [domain]=lbdb3.balance.rbt [host]=lbdb3 [dns]="10.0.0.21, 8.8.8.8" )
declare -A pgpool=( [ip]=10.0.10.31 [domain]=pgpool.balance.rbt [host]=pgpool [dns]="10.0.0.21, 8.8.8.8" )
declare -A dcfreeipa=( [ip]=10.0.10.10 [domain]=dcfreeipa.balance.rbt [host]=dcfreeipa [dns]="10.0.0.21, 8.8.8.8" )

if id "$main_user" >/dev/null 2>&1; then
    echo "$main_user:$pass" | chpasswd 2>/dev/null
    chfn -f "" "$main_user"
    for group in ${ugroups[*]}; do usermod -aG $group $main_user; done
else
    useradd -m $main_user -s /bin/bash && echo "$main_user:$pass" | chpasswd 2>/dev/null
    for group in ${ugroups[*]}; do usermod -aG $group $main_user; done
fi

# if [ "$1" = "database1" ]; then
#     ip=$database1
# elif [ "$1" = "database2" ]; then
#     ip=$database2
# elif [ "$1" = "database3" ]; then
#     ip=$database3
# elif [ "$1" = "lbdb1" ]; then
#     ip=$lbdb1
# elif [ "$1" = "lbdb2" ]; then
#     ip=$lbdb2
# elif [ "$1" = "lbdb3" ]; then
#     ip=$lbdb3
# elif [ "$1" = "pgpool" ]; then
#     ip=$pgpool
# elif [ "$1" = "dcfreeipa" ]; then
#     ip=$dcfreeipa
# fi

# cat << EOF > /etc/network/interfaces
# auto eth1 
# iface eth1 inet static
#     address $ip
#     netmask 255.255.255.0
#     gateway 192.168.60.1
# EOF

# sudo systemctl restart networking

if [ "$1" = "database1" ]; then
    ip=${database1[ip]}
    dns="${database1[dns]}"
elif [ "$1" = "database2" ]; then
    ip=${database2[ip]}
    dns="${database2[dns]}"
elif [ "$1" = "database3" ]; then
    ip=${database3[ip]}
    dns="${database3[dns]}"
elif [ "$1" = "lbdb1" ]; then
    ip=${lbdb1[ip]}
    dns="${lbdb1[dns]}"
elif [ "$1" = "lbdb2" ]; then
    ip=${lbdb2[ip]}
    dns="${lbdb2[dns]}"
elif [ "$1" = "lbdb3" ]; then
    ip=${lbdb3[ip]}
    dns="${lbdb3[dns]}"
elif [ "$1" = "pgpool" ]; then
    ip=${pgpool[ip]}
    dns="${pgpool[dns]}"
elif [ "$1" = "dcfreeipa" ]; then
    ip=${dcfreeipa[ip]}
    dns="${dcfreeipa[dns]}"
fi


if [ "$1" = "network" ]; then
    nmcli connection modify "${net_name}" ipv4.method manual ip4 $ip/$vbox_subnet_mask
    nmcli connection modify "${net_name}" gw4 $vbox_gateway
    nmcli connection modify "${net_name}" ipv4.dns $dns
    nmcli connection down "${net_name}"
    nmcli connection up "${net_name}"
fi

