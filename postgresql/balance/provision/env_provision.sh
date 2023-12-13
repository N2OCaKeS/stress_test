#!/bin/bash

main_user=u
pass=1

# groups for 'u' user
ugroups=(\
  cdrom floppy audio dip video plugdev netdev lpadmin
  scanner astra-console astra-admin
)

database1="192.168.60.110"
database2="192.168.60.111"
database3="192.168.60.112"
lbdb1="192.168.60.120"
lbdb2="192.168.60.121"
lbdb3="192.168.60.122"
pgpool="192.168.60.130"
dcfreeipa="192.168.60.100"

if id "$main_user" >/dev/null 2>&1; then
    echo "$main_user:$pass" | chpasswd 2>/dev/null
    chfn -f "" "$main_user"
    for group in ${ugroups[*]}; do usermod -aG $group $main_user; done
else
    useradd -m $main_user -s /bin/bash && echo "$main_user:$pass" | chpasswd 2>/dev/null
    for group in ${ugroups[*]}; do usermod -aG $group $main_user; done
fi

if [ "$1" = "database1" ]; then
    ip=$database1
elif [ "$1" = "database2" ]; then
    ip=$database2
elif [ "$1" = "database3" ]; then
    ip=$database3
elif [ "$1" = "lbdb1" ]; then
    ip=$lbdb1
elif [ "$1" = "lbdb2" ]; then
    ip=$lbdb2
elif [ "$1" = "lbdb3" ]; then
    ip=$lbdb3
elif [ "$1" = "pgpool" ]; then
    ip=$pgpool
elif [ "$1" = "dcfreeipa" ]; then
    ip=$dcfreeipa
fi

cat << EOF > /etc/network/interfaces
auto eth1 
iface eth1 inet static
    address $ip
    netmask 255.255.255.0
    gateway 192.168.60.1
EOF

sudo systemctl restart networking

