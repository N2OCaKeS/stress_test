while true; do
  clear
  echo "Active OpenVPN connections: $(grep -c "Peer Connection Initiated" /var/log/openvpn/openvpn.log)"
  sleep 1
done