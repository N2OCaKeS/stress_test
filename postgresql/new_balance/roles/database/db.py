from new_balance.roles.vm_info import (DOMAIN, DOMAIN_ADMIN_PASSWORD, PASSWORD,
                           POSTGRES_DATA_PATH, POSTGRES_PORT, USERNAME,
                           VERSION_PG, VMS_DATES, VMS_GROUPS, PROVIDER)


class DatabaseVM():

    def __init__(self):
        self.provider = PROVIDER

    def settings(self, type_test="balance"):
        """Настройка БД + репликация"""
        provider = self.provider
        postgres_config_path = f'/etc/postgresql/{VERSION_PG}/contrprimer'
        postgres_data_path = POSTGRES_DATA_PATH
        postgres_archive_path = f'/var/lib/postgresql/{VERSION_PG}/archivedir'
        unit_file = f"""sudo tee /etc/systemd/system/postgresql@{VERSION_PG}-contrprimer.service > /dev/null <<EOF
[Unit]
Description=PostgreSQL Cluster contrprimer {VERSION_PG}
After=network.target

[Service]
Type=forking
User=postgres
Group=postgres
ExecStart=/usr/lib/postgresql/{VERSION_PG}/bin/pg_ctl start -D {postgres_data_path} -s -l {postgres_config_path}/logfile -o "-c config_file={postgres_config_path}/postgresql.conf"
ExecStop=/usr/lib/postgresql/{VERSION_PG}/bin/pg_ctl stop -D {postgres_data_path} -s -m fast
ExecReload=/usr/lib/postgresql/{VERSION_PG}/bin/pg_ctl reload -D {postgres_data_path} -s

[Install]
WantedBy=multi-user.target
EOF"""
        pg_hba_proto = ''
        if VERSION_PG == '11':
            pg_hba_proto = 'md5'
        elif VERSION_PG == '15':
            pg_hba_proto = 'scram-sha-256'

        if type_test == "info-sys-orel":
            postgres_privilege_command = 'echo "postgres ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/postgres && sudo chmod 0440 /etc/sudoers.d/postgres'
        else:
            postgres_privilege_command = 'sudo pdpl-user -l 0:3 -i 63 -c 0:8 postgres && \
                        sudo usermod -a -G shadow postgres && \
                        sudo setfacl -d -m u:postgres:r /etc/parsec/macdb && \
                        sudo setfacl -R -m u:postgres:r /etc/parsec/macdb && \
                        sudo setfacl -m u:postgres:rx /etc/parsec/macdb && \
                        sudo setfacl -d -m u:postgres:r /etc/parsec/capdb && \
                        sudo setfacl -R -m u:postgres:r /etc/parsec/capdb && \
                        sudo setfacl -m u:postgres:rx /etc/parsec/capdb && \
                        echo "postgres ALL=(ALL) NOPASSWD:ALL" | \
                        sudo tee /etc/sudoers.d/postgres && \
                        sudo chmod 0440 /etc/sudoers.d/postgres'

        prepare = {
            'g_database': {
                'create unit file': {
                    'command': f'sudo touch /etc/systemd/system/postgresql@{VERSION_PG}-contrprimer.service && \
                        {unit_file}',
                    'signal set': '',
                    'signal get': ''
                },
                'change postgres password': {
                    'command': 'yes postgres | sudo passwd postgres',
                    'signal set': '',
                    'signal get': ''
                },                
                'set postgres privilege': {
                    'command': postgres_privilege_command,
                    'signal set': 'Postgres privilege',
                    'signal get': ''
                },
                'stop main db': {
                    'command': f'sudo systemctl stop postgresql@{VERSION_PG}-main.service',
                    'signal set': '',
                    'signal get': ['Postgres privilege']
                },
                'create folder and change owner to postgres': {
                    'command': f'sudo mkdir {postgres_config_path} {postgres_data_path} {postgres_archive_path} && \
                        sudo chown postgres:postgres {postgres_config_path} {postgres_data_path} {postgres_archive_path}',
                    'signal set': 'Created db path',
                    'signal get': ['Postgres privilege']
                },

                'create log file': {
                    'command': f'sudo touch {postgres_config_path}/logfile && \
                        sudo chown postgres:postgres {postgres_config_path}/logfile',
                    'signal set': '',
                    'signal get': ['Database created']
                },
                'create pg_stat_tmp via tmpfiles': {
                    # /var/run очищается при перезагрузке — регистрируем каталог в tmpfiles.d
                    # чтобы он создавался автоматически при каждом старте системы
                    # /var/run is cleared on reboot — register dir in tmpfiles.d
                    # so it gets created automatically on every system start
                    'command': f'echo "d /var/run/postgresql/{VERSION_PG}-contrprimer.pg_stat_tmp 0700 postgres postgres -" | \
                        sudo tee /etc/tmpfiles.d/postgresql-contrprimer.conf && \
                        sudo systemd-tmpfiles --create /etc/tmpfiles.d/postgresql-contrprimer.conf',
                    'signal set': '',
                    'signal get': ['Database created']
                },

                'init db': {
                    'command': f'sudo pg_createcluster {VERSION_PG} contrprimer --datadir={postgres_data_path} --port={POSTGRES_PORT} -- --data-checksums',
                    'signal set': 'Database created',
                    'signal get': ['Created db path']
                },
                'kinit': {
                    'command': f'yes {DOMAIN_ADMIN_PASSWORD}| sudo kinit admin',
                    'signal set': 'kinit',
                    'signal get': ''
                },
                'get keytable freeipa': {
                    'command': f'sudo ipa-getkeytab --principal=postgres/$(hostname)@{DOMAIN.upper()} --keytab=/etc/postgresql/krb5.keytab && sudo chown postgres:postgres /etc/postgresql/krb5.keytab',
                    'signal set': '',
                    'signal get': ['kinit']
                },
            },
            'g_replica': {
                'del db data': {
                    'command': f'sudo rm -rf {postgres_data_path} && sudo mkdir {postgres_data_path} && sudo chown postgres:postgres {postgres_data_path} && sudo chmod 700 {postgres_data_path}',
                    'signal set': '',
                    'signal get': ['Database created']
                },
            }
        }
        provider.execute(commands=prepare, vms_dates=VMS_DATES,
                         vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD,)

        sed_master_config = {
            'g_database': [

                # sssd.conf
                {
                    'path': '/etc/sssd/sssd.conf',
                    'old': 'allowed_uids = 0, 33, 114, fly-dm, ipaapi',
                    'new': 'allowed_uids = 0, 33, 114, fly-dm, ipaapi, postgres'
                },
                # postgresql.conf
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#wal_level = replica',
                    'new': 'wal_level = replica'
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#archive_mode = off',
                    'new': 'archive_mode = on'
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': "#archive_command = ''",
                    'new': f"archive_command = 'cp %p /var/lib/postgresql/{VERSION_PG}/contrprimer/wal_archive/%f'"
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#max_wal_senders = 10',
                    'new': 'max_wal_senders = 10'
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#wal_keep_size = 0',
                    'new': 'wal_keep_size = 512'
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#hot_standby = on',
                    'new': 'hot_standby = on'
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#krb_server_keyfile = \'FILE:${sysconfdir}/krb5.keytab\'',
                    'new': 'krb_server_keyfile = \'/etc/postgresql/krb5.keytab\''
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#krb_caseins_users = off',
                    'new': 'krb_caseins_users = true'
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#wal_log_hints = off',
                    'new': 'wal_log_hints = on'
                },     
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#log_min_messages = warning',
                    'new': 'log_min_messages = debug5'
                },                              


                # pg_hba.conf
                {
                    'path': f'{postgres_config_path}/pg_hba.conf',
                    'old': 'local   all             postgres                                peer',
                    'new': 'local   all             postgres                                trust'
                },
                {
                    'path': f'{postgres_config_path}/pg_hba.conf',
                    'old': 'local   all             all                                     peer',
                    'new': 'local   all             all                                     trust'
                },
                {
                    'path': f'{postgres_config_path}/pg_hba.conf',
                    'old': f'host    all             all             0.0.0.0/0            {pg_hba_proto}',
                    'new': 'host    all             all             0.0.0.0/0            trust'
                },
                {
                    'path': f'{postgres_config_path}/pg_hba.conf',
                    'old': f'host    all             all             ::1/128                 {pg_hba_proto}',
                    'new': 'host    all             all             ::1/128                 trust'
                },
                {
                    'path': f'{postgres_config_path}/pg_hba.conf',
                    'old': 'local   replication     all                                     peer',
                    'new': 'local   replication     postgres                                trust'
                },
                {
                    'path': f'{postgres_config_path}/pg_hba.conf',
                    'old': f'host    replication     all             127.0.0.1/32            {pg_hba_proto}',
                    'new': 'host    replication     postgres        0.0.0.0/0               trust'
                },
            ]
        }
        provider.sed(sed_conf=sed_master_config, vms_dates=VMS_DATES,
                     vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)
        print("SED!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        pg_ident = {
            'g_database': {
                'ident user0': {
                    'command': f'echo \'freeipa_map        user0@{DOMAIN}               user0\' | sudo tee -a {postgres_config_path}/pg_ident.conf',
                    'signal set': '0',
                    'signal get': ''
                },
                'ident user1': {
                    'command': f'echo \'freeipa_map        user1@{DOMAIN}               user1\' | sudo tee -a {postgres_config_path}/pg_ident.conf',
                    'signal set': '1',
                    'signal get': ['0']
                },
                'ident user2': {
                    'command': f'echo \'freeipa_map        user2@{DOMAIN}               user2\' | sudo tee -a {postgres_config_path}/pg_ident.conf',
                    'signal set': '2',
                    'signal get': ['1']
                },
                'ident user3': {
                    'command': f'echo \'freeipa_map        user3@{DOMAIN}               user3\' | sudo tee -a {postgres_config_path}/pg_ident.conf',
                    'signal set': '3',
                    'signal get': ['2']
                },
                'restart sssd': {
                    'command': 'sudo systemctl restart sssd',
                    'signal set': '',
                    'signal get': ['3']
                },

            }
        }
        provider.execute(commands=pg_ident, vms_dates=VMS_DATES,
                         vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

        scp_sql = {
            'database1': [
                {
                    'mode': 'push',
                    'path_host': './new_balance/roles/database/template/contrprimer.sql',
                    'path_vm': '/tmp/contrprimer.sql'
                }
            ]
        }
        provider.scp(scp_settings=scp_sql, vms_dates=VMS_DATES,
                     username=USERNAME, password=PASSWORD)

        start_cluster = {
            'database1': {
                'start db': {
                    'command': f'sudo systemctl enable postgresql@{VERSION_PG}-contrprimer && \
                            sudo systemctl start postgresql@{VERSION_PG}-contrprimer',
                    'signal set': 'Start master',
                    'signal get': ''
                },
                'create db': {
                    'command': f'sudo su - postgres -c "psql -p {POSTGRES_PORT} -c \'CREATE DATABASE contrprimer;\'" && sudo su - postgres -c "psql -p {POSTGRES_PORT} -d contrprimer -c \'CREATE EXTENSION pgpool_recovery;\'"',
                    'signal set': 'CreateDB',
                    'signal get': ['Start master']
                },                
                'create user': {
                    'command': f'sudo su - postgres -c "psql -p {POSTGRES_PORT} -c \'CREATE ROLE user0 LOGIN; CREATE ROLE user1 LOGIN; CREATE ROLE user2 LOGIN; CREATE ROLE user3 LOGIN;\'"',
                    'signal set': 'CreateDB user',
                    'signal get': ['CreateDB']
                },
                'create schema': {
                    'command': f'sudo su - postgres -c "psql -p {POSTGRES_PORT} -d contrprimer -c \'CREATE SCHEMA s1 AUTHORIZATION user0;\'"',
                    'signal set': 'schema',
                    'signal get': ['CreateDB user']
                },                
                'filling db': {
                    'command': f'sudo su - postgres -c "psql -p {POSTGRES_PORT} -d contrprimer -f /tmp/contrprimer.sql"',
                    'signal set': 'filling',
                    'signal get': ['schema']
                },                
                
                "pgbench manual": {
                    "command": f"sudo su - postgres -c \"psql -p {POSTGRES_PORT} -c \'CREATE DATABASE test;\'\"",
                    "signal set": "pgbench manual",
                    "signal get": ['database2', "repl start"]
                },
                # Создаём таблицу для теста балансировки — чтобы clients.py не делал это сам при старте
                # Create test table for load balancing test — so clients.py doesn't do it on startup
                'create test table': {
                    'command': f'sudo su - postgres -c "psql -p {POSTGRES_PORT} -d test -c \'CREATE TABLE IF NOT EXISTS test (value BIGINT PRIMARY KEY, time TIMESTAMPTZ NOT NULL DEFAULT now());\'"',
                    'signal set': 'test table created',
                    'signal get': ['pgbench manual']
                },
            },

            'g_replica': {
                'replication': {
                    'command': f'sudo su - postgres -c "pg_basebackup -h {VMS_DATES['database1']['ip_bridge']} -p {POSTGRES_PORT} -U postgres -D {postgres_data_path} -Fp -Xs -P -R --wal-method=stream"',
                    'signal set': 'Replication success',
                    'signal get': ['database1', 'schema']
                },              
                'start replica': {
                    'command': f'sudo systemctl enable postgresql@{VERSION_PG}-contrprimer && \
                            sudo systemctl start postgresql@{VERSION_PG}-contrprimer',
                    'signal set': 'repl start',
                    'signal get': ['Replication success']
                }
            },
        }

        provider.execute(commands=start_cluster, vms_dates=VMS_DATES,
                         vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)
        wal_folder = {
            'g_database':{

                'create wal folder': {
                    'command': f'sudo mkdir -p {postgres_data_path}/wal_archive && \
                        sudo chown postgres:postgres {postgres_data_path}/wal_archive',
                    'signal set': '',
                    'signal get': ''
                },

            }
        }

        provider.execute(commands=wal_folder, vms_dates=VMS_DATES,
                         vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

    def setup_protopack(self, type_test="info-sys"):
        """Создаёт БД protopack внутри кластера contrprimer (порт POSTGRES_PORT) и наполняет её
        данными из ftp://10.177.103.205/upload/ — источник тот же, что в psb_db_prep_stand12_olap.sh.
        Вызывать после settings(), только для type_test == "info-sys": setup_mac()
        расставляет MAC-метки на этих таблицах уже после того, как эта функция отработает."""
        provider = self.provider

        scp_protopack = {
            'database1': [
                {
                    'mode': 'push',
                    'path_host': './new_balance/roles/database/template/protopack_schema.sql',
                    'path_vm': '/tmp/protopack_schema.sql'
                }
            ]
        }
        provider.scp(scp_settings=scp_protopack, vms_dates=VMS_DATES,
                     username=USERNAME, password=PASSWORD)

        if type_test == "info-sys-orel":
            protopack_prereq = {
                'plain protopack prereq': {
                    'command': 'true',
                    'signal set': 'protopack prerequisite ready',
                    'signal get': ''
                },
            }
        else:
            protopack_prereq = {
                'grant chmac privilege': {
                    # Нужно только для info-sys (МРД на protopack): setup_mac()
                    # меняет метки существующих строк через CHMAC, а это требует привилегии
                    # ac_capable_chmac (PARSEC_CAP_CHMAC), которая не выдаётся в settings().
                    # Привилегия применяется с новой сессии postgres, поэтому выдаём её здесь,
                    # до первого su - postgres в этом методе.
                    'command': 'sudo usercaps -m PARSEC_CAP_CHMAC postgres',
                    'signal set': 'protopack prerequisite ready',
                    'signal get': ''
                },
            }

        protopack_access = {}
        if type_test == "info-sys-orel":
            protopack_access = {
                'grant plain protopack access': {
                    'command': (
                        "sudo tee /tmp/plain_protopack_access.sql > /dev/null <<'SQL'\n"
                        "DO $$\n"
                        "BEGIN\n"
                        "    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'protopack_web') THEN\n"
                        "        CREATE ROLE protopack_web LOGIN;\n"
                        "    END IF;\n"
                        "END\n"
                        "$$;\n"
                        "GRANT CONNECT ON DATABASE protopack TO protopack_web;\n"
                        "GRANT USAGE ON SCHEMA main TO protopack_web;\n"
                        "GRANT SELECT ON ALL TABLES IN SCHEMA main TO protopack_web;\n"
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA main GRANT SELECT ON TABLES TO protopack_web;\n"
                        "GRANT USAGE ON SCHEMA other TO protopack_web;\n"
                        "GRANT SELECT ON ALL TABLES IN SCHEMA other TO protopack_web;\n"
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA other GRANT SELECT ON TABLES TO protopack_web;\n"
                        "SQL\n"
                        f"sudo su - postgres -c \"psql -p {POSTGRES_PORT} -d protopack -f /tmp/plain_protopack_access.sql\""
                    ),
                    'signal set': 'plain protopack access granted',
                    'signal get': ['protopack imported']
                },
            }

        protopack = {
            'database1': {
                **protopack_prereq,
                'create protopack db': {
                    # Сигналы Libvirt.execute живут только в рамках одного вызова execute(),
                    # поэтому 'CreateDB' из settings() (отдельный вызов) сюда не пробрасывается.
                    # Зависимость не нужна: контракт метода — вызывать после settings(),
                    # когда кластер contrprimer уже поднят.
                    'command': f'sudo su - postgres -c "createdb -p {POSTGRES_PORT} --encoding=UTF8 --locale=C --template=template0 protopack"',
                    'signal set': 'protopack db created',
                    'signal get': ['protopack prerequisite ready']
                },
                'protopack schema': {
                    'command': f'sudo su - postgres -c "psql -p {POSTGRES_PORT} -d protopack -f /tmp/protopack_schema.sql"',
                    'signal set': 'protopack schema',
                    'signal get': ['protopack db created']
                },
                'download protopack data': {
                    'command': 'sudo wget -P /tmp ftp://10.177.103.10/postgresql/build_*',
                    'signal set': 'protopack data downloaded',
                    'signal get': ['protopack schema'],
                    'nowait': True,
                    'nowait_mode': 'continue',
                    'nowait_timeout': 600,
                },
                'import build_info': {
                    # 'protopack data downloaded' ставится через 10с после запуска wget (nowait/continue),
                    # а не после реального завершения — поэтому здесь ждём, пока процесс wget
                    # действительно закончит скачивание всех build_* файлов.
                    'command': f'for i in $(seq 1 120); do pgrep -f "wget -P /tmp ftp://10.177.103.10/postgresql/build_" > /dev/null || break; sleep 5; done; \
                        if [ -f /tmp/build_info ]; then sudo su - postgres -c "psql -p {POSTGRES_PORT} -d protopack -f /tmp/build_info"; else echo "build_info not found, skip"; fi',
                    'signal set': 'protopack build_info imported',
                    'signal get': ['protopack data downloaded']
                },
                'import build_packages_new': {
                    'command': f'if [ -f /tmp/build_packages_new ]; then sudo su - postgres -c "psql -p {POSTGRES_PORT} -d protopack -f /tmp/build_packages_new"; else echo "build_packages_new not found, skip"; fi',
                    'signal set': 'protopack build_packages imported',
                    'signal get': ['protopack build_info imported']
                },
                'import build_sourses': {
                    'command': f'if [ -f /tmp/build_sourses ]; then sudo su - postgres -c "psql -p {POSTGRES_PORT} -d protopack -f /tmp/build_sourses"; else echo "build_sourses not found, skip"; fi',
                    'signal set': 'protopack imported',
                    'signal get': ['protopack build_packages imported']
                },
                **protopack_access,
            },
        }
        # timeout — верхняя граница ожидания сигнала в минутах (проверяет каждые 10с и возвращается сразу, как только сигнал появился)
        provider.execute(commands=protopack, vms_dates=VMS_DATES, vms_groups=VMS_GROUPS,
                         username=USERNAME, password=PASSWORD, timeout=180)

    def setup_mac(self):
        """Настройка MAC-меток на таблицах protopack и создание сервисного пользователя protopack_web"""
        provider = self.provider

        os_account = {
            "g_database": {
                "create protopack_web os account": {
                    "command": (
                        "id protopack_web > /dev/null 2>&1 || "
                        "(sudo useradd --no-create-home --shell /usr/sbin/nologin protopack_web && "
                        "sudo pdpl-user -l 0:3 -i 63 -c 0:8 protopack_web)"
                    ),
                    "signal set": "protopack_web os account ready",
                    "signal get": "",
                },
            }
        }
        provider.execute(commands=os_account, vms_dates=VMS_DATES, vms_groups=VMS_GROUPS,
                         username=USERNAME, password=PASSWORD)

        scp_mac_setup = {
            # Только primary: database2/database3 — read-only реплики contrprimer,
            # изменения (MAC-метки, CREATE USER) разъедутся на них через WAL-репликацию.
            "database1": [
                {
                    "mode": "push",
                    "path_host": "./new_balance/roles/database/template/mac_setup.sql",
                    "path_vm": "/tmp/mac_setup.sql",
                }
            ]
        }
        provider.scp(scp_settings=scp_mac_setup, vms_dates=VMS_DATES,
                     username=USERNAME, password=PASSWORD)

        commands = {
            "database1": {
                "apply mac labels to protopack": {
                    "command": f'sudo su - postgres -c "psql -p {POSTGRES_PORT} -d protopack -f /tmp/mac_setup.sql"',
                    "signal set": "",
                    "signal get": "",
                },
            }
        }
        provider.execute(
            commands=commands,
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )

    def setup_privsock(self):
        """Даёт постгресу привилегию PARSEC_CAP_PRIV_SOCK так, чтобы она реально
        применялась к процессу. Без этого слушающий сокет contrprimer создаётся
        с МРД-меткой уровня 0 (метка процесса на момент bind()/listen() никогда
        не поднимается), и ядро (parsec_sock_rcv) молча отбрасывает любое входящее
        соединение с ненулевой меткой — это выглядит как обычный connect()-таймаут.


        Выставляет ac_ignore_socket_maclabel = false: без этого Postgres
        игнорирует метку входящего соединения при определении метки сессии, и
        построчная МРД-фильтрация (CHMAC-метки в mac_setup.sql) не работает"""
        provider = self.provider
        postgres_config_path = f'/etc/postgresql/{VERSION_PG}/contrprimer'

        commands = {
            "g_database": {
                "grant priv_sock to postgres": {
                    "command": "sudo usercaps -m PARSEC_CAP_PRIV_SOCK postgres",
                    "signal set": "privsock granted",
                    "signal get": "",
                },
                "create pam service for postgres": {
                    "command": (
                        "sudo tee /etc/pam.d/postgresql-contrprimer > /dev/null <<'EOF'\n"
                        "account required pam_permit.so\n"
                        "session required pam_parsec_cap.so\n"
                        "EOF"
                    ),
                    "signal set": "pam service created",
                    "signal get": ["privsock granted"],
                },
                "add PAMName to unit": {
                    "command": (
                        f"grep -q '^PAMName=' /etc/systemd/system/postgresql@{VERSION_PG}-contrprimer.service || "
                        f"sudo sed -i '/^Type=forking/a PAMName=postgresql-contrprimer' "
                        f"/etc/systemd/system/postgresql@{VERSION_PG}-contrprimer.service"
                    ),
                    "signal set": "pamname added",
                    "signal get": ["pam service created"],
                },
                "enforce socket maclabel for row security": {
                    "command": (
                        f"sudo sed -i 's/ac_ignore_socket_maclabel = true/ac_ignore_socket_maclabel = false/' "
                        f"{postgres_config_path}/postgresql.conf"
                    ),
                    "signal set": "socket maclabel enforced",
                    "signal get": ["pamname added"],
                },
                "reload and restart contrprimer": {
                    "command": (
                        "sudo systemctl daemon-reload && "
                        f"sudo systemctl restart postgresql@{VERSION_PG}-contrprimer"
                    ),
                    "signal set": "",
                    "signal get": ["socket maclabel enforced"],
                },
            }
        }
        provider.execute(
            commands=commands,
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )
