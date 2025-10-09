#!/bin/bash

set -vx

sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install htop -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install ssh git resolvconf sysstat -y
sudo timedatectl set-ntp true

iface=`ip a | grep '2: ' | awk '{print$2}' | tr -d ':' | head -n 1`

ip=$1
dns="10.177.180.246 10.177.128.198"
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

echo "u  ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/u
echo "u  ALL=(ALL:ALL) NOPASSWD:ALL" | sudo tee -a /etc/sudoers
