# Тестовый SSH-таргет для dev-стека: обычный Debian с привилегированным
# sshd (root). В отличие от linuxserver/openssh-server (Alpine, unprivileged
# sshd + locked useradd) здесь prepare реально отрабатывает: worker заходит по
# паролю bootstrap-юзера, заводит управляющего dbos-пользователя через useradd
# + sudoers, проверяет вход по ключу и хардит sshd (PasswordAuthentication no /
# PermitRootLogin no). dpkg-query на месте — installed_packages.list возвращает
# реальный список пакетов.
FROM debian:stable-slim

# openssh-server — сам демон; sudo — для NOPASSWD-правила управляющего юзера;
# passwd даёт useradd/usermod/chpasswd; iproute2/procps пригодятся для отладки
# изнутри контейнера.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openssh-server \
        sudo \
        passwd \
        procps \
        iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Слушаем на 2222 — тот же порт, что ждёт seed (TEST_SERVER_SSH_PORT) и что
# проброшен наружу в compose. Privsep-каталог нужен sshd при старте.
RUN mkdir -p /run/sshd \
    && sed -i 's/^#\?Port .*/Port 2222/' /etc/ssh/sshd_config

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 2222

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
