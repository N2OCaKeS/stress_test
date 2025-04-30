#!/usr/bin/env bash
set -euo pipefail
set -x                                           # [1] включаем вывод команд
trap 'echo "❌ Ошибка на строке $LINENO: команда «$BASH_COMMAND» вернула код $?."' ERR  
                                                 # [2] выводим, что именно упало

# 1. Базовые обновления и нужные пакеты
sudo apt update
sudo apt install -y htop gcc make astra-openvpn-server

# 2. Astra-specific
# sudo astra-update -A -T -r

# 3. Сбор параметров
if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <host> <kernel-suffix>"
  exit 1
fi
host="$1"
kernel="$2"

# 5. Массивы конфигураций
declare -A vpn1=( [ip]=10.0.0.11 [dns]="10.0.0.10,8.8.8.8" )
declare -A vpn2=( [ip]=10.0.0.12 [dns]="10.0.0.10,8.8.8.8" )
declare -A balancer=( [ip]=10.0.0.13 [dns]="10.0.0.10,8.8.8.8" )

# 6. Настройка сети через nmcli
conf="${host}[ip]"
dnsconf="${host}[dns]"
eval ip_addr=\${$conf}
eval dns_addr=\${$dnsconf}

nat_net_name="Wired connection 1"
vbox_bridge_mask=24
vbox_bridge_gateway=10.177.103.254

nmcli connection modify "${nat_net_name}" \
  ipv4.method manual ip4 "${ip_addr}/${vbox_bridge_mask}" \
  gw4 "${vbox_bridge_gateway}" \
  ipv4.dns "${dns_addr}"
nmcli connection up "${nat_net_name}"

# 8. SSH и sudo
sudo sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' \
  /etc/ssh/sshd_config
echo "root:$(openssl rand -base64 12)" | sudo chpasswd

cat <<EOF | sudo tee /etc/sudoers.d/u
u ALL=(ALL) NOPASSWD:ALL
postgres ALL=(ALL) NOPASSWD:ALL
EOF

# 9. Установка пакета ядра
if [[ "$kernel" =~ ^[0-9]+\.[0-9]+\.[0-9]+-(generic|lowlatency)$ ]]; then
  suffix="${BASH_REMATCH[1]}"
  version_major_minor=$(echo "${kernel%%-*}" | awk -F. '{print $1"."$2}')
  pkg="linux-image-${version_major_minor}-${suffix}"
  sudo apt install -y "${pkg}"
else
  echo "Неправильный формат ядра: $kernel"
  exit 1
fi

# 10. GRUB
sudo grub-set-default "Advanced options for Ubuntu>Ubuntu, with Linux ${kernel}"
sudo update-grub
