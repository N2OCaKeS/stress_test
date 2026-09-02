#!/bin/sh
# Старт тестового SSH-сервера для dev-стека.
#
# При первом старте заводим bootstrap-юзера с паролем и в группе sudo — под
# ним prepare заходит по паролю на ещё неуправляемый бокс. Стартовый
# sshd_config держим максимально открытым (PasswordAuthentication yes,
# PermitRootLogin yes), чтобы bootstrap прошёл; hardening после prepare
# доложит worker drop-in'ом в /etc/ssh/sshd_config.d/.
set -e

BOOTSTRAP_USER="${BOOTSTRAP_USER:-tester}"
BOOTSTRAP_PASSWORD="${BOOTSTRAP_PASSWORD:-tester1234}"

# Bootstrap-юзер: создаём идемпотентно (контейнер мог рестартовать с тем же
# state'ом, хотя обычно он эфемерный). bash как login shell — на нём worker
# гоняет bash -c '...' команды.
if ! id "$BOOTSTRAP_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash -G sudo "$BOOTSTRAP_USER"
fi
echo "${BOOTSTRAP_USER}:${BOOTSTRAP_PASSWORD}" | chpasswd

# Базовая SSH-политика на старте: пароль и root-login разрешены — иначе
# bootstrap-сессия prepare не зайдёт. Пишем своим файлом в sshd_config.d, а в
# основном конфиге раскомментируем Include, если он закрыт (на части debian-
# сборок он закомментирован). Worker позже положит сюда свой hardening-drop-in
# с большим лексикографическим приоритетом не понадобится — он перетрёт эти
# значения, т.к. в sshd «первое вхождение выигрывает», поэтому свой файл
# называем 00-, чтобы worker'ский <user>-dbos.conf шёл раньше и побеждал.
mkdir -p /etc/ssh/sshd_config.d
if grep -qE '^[[:space:]]*#[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config.d/\*\.conf' /etc/ssh/sshd_config; then
    sed -i -E 's|^[[:space:]]*#[[:space:]]*(Include[[:space:]]+/etc/ssh/sshd_config.d/\*\.conf)|\1|' /etc/ssh/sshd_config
fi
cat > /etc/ssh/sshd_config.d/zz-bootstrap.conf <<'EOF'
# Стартовая (открытая) политика тестового сервера. Worker после prepare
# кладёт <management_user>-dbos.conf, который идёт раньше по сортировке
# Include и перекрывает эти значения (sshd: первое вхождение выигрывает).
PasswordAuthentication yes
PermitRootLogin yes
PubkeyAuthentication yes
EOF
chmod 644 /etc/ssh/sshd_config.d/zz-bootstrap.conf

# Хост-ключи генерим, если их ещё нет (свежий образ без ключей).
ssh-keygen -A

# sshd на переднем плане — PID 1 контейнера, чтобы docker видел живость
# процесса и корректно гасил его по SIGTERM.
exec /usr/sbin/sshd -D -e
