#! /bin/bash

set -Eeuo pipefail

CONNECT_NUMBER=$1
client_dir=/home/u/openvpn/clients_keys/tester$CONNECT_NUMBER
client_cfg=$client_dir/client.ovpn
nohup=$client_dir/nohup.out
oct2=$((31 + $CONNECT_NUMBER / 256))
oct3=$(($CONNECT_NUMBER % 256))
ip="172.${oct2}.${oct3}"
cd /home/u

if [[ ! -f "$client_cfg" ]]; then
  echo "ERROR: client config missing: $client_cfg" >&2
  exit 1
fi

sudo /home/u/vpn.sh stop vpn$CONNECT_NUMBER >/dev/null 2>&1 || true
sudo /home/u/vpn.sh start vpn$CONNECT_NUMBER $ip --no-tmux

if ! sudo ip netns list | awk '{print $1}' | grep -qx "vpn$CONNECT_NUMBER"; then
  echo "ERROR: netns vpn$CONNECT_NUMBER was not created" >&2
  exit 1
fi

# Start OpenVPN client
sudo ip netns exec vpn$CONNECT_NUMBER bash -lc "
  cd $client_dir
  nohup openvpn --config client.ovpn --dev tun$CONNECT_NUMBER --auth-nocache --resolv-retry 0 --connect-retry-max 1 --connect-timeout 10 --ping 10 --ping-restart 60 --remap-usr1 SIGTERM \
    & disown -a
"

tun_dev="tun$CONNECT_NUMBER"
tun_ready=0
for _ in {1..120}; do
  if sudo ip netns exec vpn$CONNECT_NUMBER ip link show dev "$tun_dev" >/dev/null 2>&1; then
    tun_ready=1
    break
  fi
  sleep 0.5
done

if [[ "$tun_ready" -ne 1 ]]; then
  echo "WARN: $tun_dev not ready, skipping iperf" >&2
  sudo cat "$nohup" || true
  exit 0
fi

sudo ip netns exec vpn$CONNECT_NUMBER bash -lc "
  nohup iperf -c 10.8.0.1 -u -b 5G -t 3600 -i 1 \
  & disown -a
"
sudo cat "$nohup" || true
