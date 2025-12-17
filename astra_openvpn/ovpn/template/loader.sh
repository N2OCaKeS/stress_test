#! /bin/bash

set -Eeuo pipefail

CONNECT_NUMBER=$1
client_dir=/home/u/openvpn/clients_keys/tester$CONNECT_NUMBER
nohup=$client_dir/nohup.out
oct2=$((31 + $CONNECT_NUMBER / 256))
oct3=$(($CONNECT_NUMBER % 256))
ip="172.${oct2}.${oct3}"
cd /home/u
./vpn.sh stop vpn$CONNECT_NUMBER
./vpn.sh start vpn$CONNECT_NUMBER $ip --no-tmux

# Start OpenVPN client
ip netns exec vpn$CONNECT_NUMBER bash -lc "
  cd $client_dir
  nohup openvpn --config client.ovpn \
    --dev tun$CONNECT_NUMBER --auth-nocache \
    --resolv-retry 0 --connect-retry-max 1 --connect-timeout 10 \
    --ping 10 --ping-exit  --remap-usr1 SIGTERM \
    & disown -a
"
cat $nohup

# Load start
ip netns exec vpn$CONNECT_NUMBER bash -lc "nohup iperf -c 10.8.0.1 -u -b 16M -t \$((60*60)) -i 5 & disown -a"
cat $nohup