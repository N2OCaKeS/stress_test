#!/bin/bash

main_user=u
pass=1

# groups for 'u' user
ugroups=(\
  cdrom floppy audio dip video plugdev netdev lpadmin
  scanner astra-console astra-admin
)

db1="192.168.60.110"
db2="192.168.60.111"
db3="192.168.60.112"

if id "$main_user" >/dev/null 2>&1; then
    echo "$main_user:$pass" | chpasswd 2>/dev/null
    chfn -f "" "$main_user"
    for group in ${ugroups[*]}; do usermod -aG $group $main_user; done
else
    useradd -m $main_user -s /bin/bash && echo "$main_user:$pass" | chpasswd 2>/dev/null
    for group in ${ugroups[*]}; do usermod -aG $group $main_user; done
fi

if [ "$1" = "db1" ]; then
    ip=$db1
elif [ "$1" = "db2" ]; then
    ip=$db2
elif [ "$1" = "db3" ]; then
    ip=$db3
fi

cat << EOF > /etc/network/interfaces
auto eth1 
iface eth1 inet static
    address $ip
    netmask 255.255.255.0
    gateway 192.168.60.1
EOF

sudo systemctl restart networking

