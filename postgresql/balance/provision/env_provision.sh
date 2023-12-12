#!/bin/bash

main_user=u
pass=1

# groups for 'u' user
ugroups=(\
  cdrom floppy audio dip video plugdev netdev lpadmin
  scanner astra-console astra-admin
)

if id "$main_user" >/dev/null 2>&1; then
    echo "$main_user:$pass" | chpasswd 2>/dev/null
    chfn -f "" "$main_user"
    for group in ${ugroups[*]}; do usermod -aG $group $main_user; done
else
    useradd -m $main_user -s /bin/bash && echo "$main_user:$pass" | chpasswd 2>/dev/null
    for group in ${ugroups[*]}; do usermod -aG $group $main_user; done
fi

if [ "$1" = "db1" ]; then
    ip="192.168.60.10"
elif [ "$1" = "db2" ]; then
    ip="192.168.60.11"
elif [ "$1" = "db3" ]; then
    ip="192.168.60.12"
fi

cat << EOF > /etc/network/interfaces
auto eth0 
iface eth0 inet static
    address $ip
    netmask 255.255.255.0
    gateway 192.168.60.1
EOF

sudo ifdown eth0 && sudo ifup eth0

