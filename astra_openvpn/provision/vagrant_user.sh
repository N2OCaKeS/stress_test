#!/bin/bash
set -eux

# 1. Пользователь vagrant
if ! id vagrant &>/dev/null; then
  useradd -m -s /bin/bash vagrant
fi

# 2. Назначение пароля
echo 'vagrant:vagrant' | chpasswd || true

# 3. Разрешаем вход по паролю по SSH
sed -i 's/^#*PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# 4. Перезапуск SSH
if command -v systemctl &>/dev/null; then
  systemctl restart sshd
fi
