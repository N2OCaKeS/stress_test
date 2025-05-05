#!/bin/bash
set -eux

# 1. Пользователь vagrant
if ! id vagrant &>/dev/null; then
  useradd -m -s /bin/bash vagrant
fi

# 2. Назначение пароля
echo 'vagrant:vagrant' | chpasswd

# 3. Разрешаем вход по паролю по SSH
sed -i 's/^#*PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# 4. Перезапуск SSH
if command -v systemctl &>/dev/null; then
  systemctl restart sshd
else
  service ssh restart || service sshd restart
fi

# 5. Настройка сети для vpn1
HOSTNAME=$(hostname)

if [[ "$HOSTNAME" == "vpn1.stress.rbt" ]]; then
  echo "Настройка статического IP для vpn1..."

  IFACE=$(ip -o -4 addr show | awk '$4 ~ /192\.168\.50\./ {print $2}' | head -n1)

  ip addr flush dev "$IFACE" || true
  ip addr add 192.168.50.121/24 dev "$IFACE"
  ip link set "$IFACE" up
  ip route add default via 192.168.50.1 dev "$IFACE" || true

  echo "vpn1: интерфейс $IFACE настроен."
fi


apt-get update -y
apt-get install -y qemu-guest-agent
systemctl start qemu-guest-agent
