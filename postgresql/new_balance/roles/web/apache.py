from new_balance.roles.vm_info import (
    VMS_DATES, VMS_GROUPS, USERNAME, PASSWORD, PROVIDER, DOMAIN, PGPOOL_HOSTNAME, POSTGRES_PORT, DOMAIN_ADMIN_USER, DOMAIN_ADMIN_PASSWORD
)

APP_DIR = "/opt/protopack"
KEYTAB  = "/etc/apache2/protopack.keytab"


class ApacheVM:
    def __init__(self):
        self.provider = PROVIDER

    def settings(self):
        """Разворачивает Protopack (Flask/WSGI) на web1 в мандатном режиме Apache.

        Требования к порядку вызова: должен выполняться после DomainVM.settings()
        (web1 уже в домене, служба HTTP/web1.{DOMAIN} зарегистрирована в FreeIPA)
        и после DatabaseVM.setup_mac() (MAC-метки и роль protopack_web уже созданы).

        WSGI работает во встроенном режиме (без WSGIDaemonProcess) на mpm_prefork —
        см. пояснение в conf/protopack.conf, почему daemon mode ломает МРД.
        """
        provider = self.provider

        scp_app = {
            "web1": [
                {
                    "mode": "push",
                    "path_host": "./new_balance/roles/web/app",
                    "path_vm": "/tmp/protopack_app",
                },
                {
                    "mode": "push",
                    "path_host": "./new_balance/roles/web/conf/protopack.conf",
                    "path_vm": "/tmp/protopack.conf",
                },
            ]
        }
        provider.scp(
            scp_settings=scp_app,
            vms_dates=VMS_DATES,
            username=USERNAME,
            password=PASSWORD,
        )

        commands = {
            "g_web": {
                "install dependencies": {
                    "command": "sudo apt-get install -y apache2 libapache2-mod-wsgi-py3 libapache2-mod-auth-gssapi python3-pip",
                    "signal set": "apt done",
                    "signal get": "",
                },
                # AstraMode привязывает MAC-метку к процессу, принявшему соединение,
                # поэтому Apache должен работать на mpm_prefork (1 соединение = 1 процесс),
                # а не на threaded event/worker MPM, где один процесс обслуживает всех.
                "switch to mpm_prefork": {
                    "command": (
                        "sudo a2dismod mpm_event mpm_worker >/dev/null 2>&1; "
                        "sudo a2enmod mpm_prefork"
                    ),
                    "signal set": "mpm prefork",
                    "signal get": "apt done",
                },
                "enable apache modules": {
                    "command": "sudo a2enmod auth_gssapi wsgi && sudo systemctl enable apache2",
                    "signal set": "modules enabled",
                    "signal get": "mpm prefork",
                },
                "enable astramode": {
                    "command": (
                        "sudo sh -c \""
                        "if grep -q '^AstraMode' /etc/apache2/apache2.conf; then "
                        "sed -i 's/^AstraMode.*/AstraMode on/' /etc/apache2/apache2.conf; "
                        "elif grep -q '^#\\s*AstraMode' /etc/apache2/apache2.conf; then "
                        "sed -i 's/^#\\s*AstraMode.*/AstraMode on/' /etc/apache2/apache2.conf; "
                        "else echo 'AstraMode on' >> /etc/apache2/apache2.conf; fi; "
                        "if grep -q '^IncludeRealm' /etc/apache2/apache2.conf; then "
                        "sed -i 's/^IncludeRealm.*/IncludeRealm on/' /etc/apache2/apache2.conf; "
                        "elif grep -q '^#\\s*IncludeRealm' /etc/apache2/apache2.conf; then "
                        "sed -i 's/^#\\s*IncludeRealm.*/IncludeRealm on/' /etc/apache2/apache2.conf; "
                        "else echo 'IncludeRealm on' >> /etc/apache2/apache2.conf; fi"
                        "\""
                    ),
                    "signal set": "astramode set",
                    "signal get": "modules enabled",
                },

                "grant www-data macdb access": {
                    "command": (
                        "sudo setfacl -d -m u:www-data:r /etc/parsec/macdb && "
                        "sudo setfacl -R -m u:www-data:r /etc/parsec/macdb && "
                        "sudo setfacl -m u:www-data:rx /etc/parsec/macdb"
                    ),
                    "signal set": "macdb acl set",
                    "signal get": "astramode set",
                },

                "mark var-www-html directory": {
                    "command": (
                        "sudo pdpl-file -u 3:0:-1:ccnr /var/www && "
                        "sudo pdpl-file -u 3:0:-1:ccnr /var/www/html"
                    ),
                    "signal set": "www-html labeled",
                    "signal get": "macdb acl set",
                },
                "deploy mrd test file": {
                    # Демонстрация классической файловой МРД Apache (без Flask/БД):
                    # файл виден только пользователям с уровнем допуска >= 1:0,
                    # остальным Apache вернёт 404 — доступ проверяет ядро/parsec,
                    # а не код приложения. Доступен по /files/secretno.html.
                    "command": (
                        "sudo tee /var/www/html/secretno.html > /dev/null <<'EOF'\n"
                        "<html><body>\n"
                        "<h1>Секретно — уровень МРД 1</h1>\n"
                        "<p>Страница видна только пользователям с мандатным уровнем допуска &gt;= 1:0.</p>\n"
                        "</body></html>\n"
                        "EOF\n"
                        "sudo pdpl-file -u 1:0:0 /var/www/html/secretno.html"
                    ),
                    "signal set": "mrd test file ready",
                    "signal get": "www-html labeled",
                },
                "kinit": {
                    "command": f"yes {DOMAIN_ADMIN_PASSWORD} | sudo kinit {DOMAIN_ADMIN_USER}",
                    "signal set": "Kinit",
                    "signal get": "mrd test file ready",
                },
                "keytab": {
                    # Служба HTTP/web1.{DOMAIN} уже зарегистрирована в DomainVM.settings()
                    "command": f"sudo ipa-getkeytab -s dcfreeipa.{DOMAIN} -p HTTP/web1.{DOMAIN} -k {KEYTAB}",
                    "signal set": "keytab fetched",
                    "signal get": "Kinit",
                },
                "secure keytab": {
                    "command": f"sudo chown www-data:www-data {KEYTAB} && sudo chmod 600 {KEYTAB}",
                    "signal set": "keytab ready",
                    "signal get": "keytab fetched",
                },
                "deploy app files": {
                    "command": (
                        f"sudo mkdir -p {APP_DIR} && "
                        f"sudo cp -r /tmp/protopack_app/. {APP_DIR}/ && "
                        f"sudo chown -R www-data:www-data {APP_DIR}"
                    ),
                    "signal set": "app deployed",
                    "signal get": "keytab ready",
                },
                "install python deps": {
                    "command": (
                        "sudo sh -c \""
                        "if test \\\"$(grep 1.7 /etc/astra_version)\\\"; then "
                        f"pip3 install -q -r {APP_DIR}/requirements.txt; "
                        "else "
                        f"pip3 install -q --break-system-packages -r {APP_DIR}/requirements.txt; "
                        "fi\""
                    ),
                    "signal set": "python deps installed",
                    "signal get": "app deployed",
                },
                "deploy apache vhost": {
                    "command": (
                        "sudo cp /tmp/protopack.conf /etc/apache2/sites-available/protopack.conf && "
                        "sudo a2ensite protopack && "
                        "sudo a2dissite 000-default"
                    ),
                    "signal set": "vhost enabled",
                    "signal get": "python deps installed",
                },
                "set db env vars": {
                    "command": (
                        f"sudo tee -a /etc/apache2/envvars > /dev/null <<'EOF'\n"
                        f"export DB_HOST={PGPOOL_HOSTNAME}\n"
                        f"export DB_PORT={POSTGRES_PORT}\n"
                        f"export DB_USER=protopack_web\n"
                        f"EOF"
                    ),
                    "signal set": "envvars set",
                    "signal get": "vhost enabled",
                },
                "restart apache": {
                    "command": "sudo systemctl restart apache2",
                    "signal set": "",
                    "signal get": "envvars set",
                },
            }
        }
        self.provider.execute(
            commands=commands,
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )
