from allta import VBoxManager
from roles.vm_info import VERSION_PG, VMS_DATES, VMS_GROUPS, POSTGRES_DATA_PATH, POSTGRES_PORT, DOMAIN, DOMAIN_ADMIN_PASSWORD


class DatabaseVM():  # TODO НАДО ПРОВЕРИТЬ!

    def __init__(self):
        self.provider = VBoxManager()

    def settings(self):
        """Настройка БД + репликация"""
        provider = self.provider
        postgres_config_path = f'/etc/postgresql/{VERSION_PG}/contrprimer'
        postgres_data_path = POSTGRES_DATA_PATH
        log = '/tmp/contrprimer'
        unit_file = f"""sudo tee /etc/systemd/system/postgresql@{VERSION_PG}-contrprimer.service > /dev/null <<EOF
[Unit]
Description=PostgreSQL Cluster contrprimer {VERSION_PG}
After=network.target

[Service]
Type=forking
User=postgres
Group=postgres
ExecStart=/usr/lib/postgresql/{VERSION_PG}/bin/pg_ctl start -D {postgres_data_path} -s -l {postgres_config_path}/logfile
ExecStop=/usr/lib/postgresql/{VERSION_PG}/bin/pg_ctl stop -D {postgres_data_path} -s -m fast
ExecReload=/usr/lib/postgresql/{VERSION_PG}/bin/pg_ctl reload -D {postgres_data_path} -s

[Install]
WantedBy=multi-user.target
EOF"""

        if VERSION_PG == '11':
            pg_hba_proto = 'md5'
        elif VERSION_PG == '15':
            pg_hba_proto = 'scram-sha-256'

        prepare = {
            'g_database': {
                'create unit file': {
                    'command': f'sudo touch /etc/systemd/system/postgresql@{VERSION_PG}-contrprimer.service && \
                        {unit_file}',
                    'signal set': '',
                    'signal get': ''
                },
                'set postgres privilege': {
                    'command': f'sudo pdpl-user -l 0:3 -i 63 -c 0:8 postgres && \
                        sudo usermod -a -G shadow postgres && \
                        sudo setfacl -d -m u:postgres:r /etc/parsec/macdb && \
                        sudo setfacl -R -m u:postgres:r /etc/parsec/macdb && \
                        sudo setfacl -m u:postgres:rx /etc/parsec/macdb && \
                        sudo setfacl -d -m u:postgres:r /etc/parsec/capdb && \
                        sudo setfacl -R -m u:postgres:r /etc/parsec/capdb && \
                        sudo setfacl -m u:postgres:rx /etc/parsec/capdb',
                    'signal set': 'Postgres privilege',
                    'signal get': ''
                },
                'stop main db': {
                    'command': f'sudo systemctl stop postgresql@{VERSION_PG}-main.service',
                    'signal set': '',
                    'signal get': ['Postgres privilege']
                },
                'create folder and change owner to postgres': {
                    'command': f'sudo mkdir {postgres_config_path} {postgres_data_path} && \
                        sudo chown postgres:postgres {postgres_config_path} {postgres_data_path}',
                    'signal set': 'Created db path',
                    'signal get': ['Postgres privilege']
                },

                'create log file': {
                    'command': f'sudo touch {postgres_config_path}/logfile && \
                        sudo chown postgres:postgres {postgres_config_path}/logfile',
                    'signal set': '',
                    'signal get': ['Database created']
                },

                # 'create wal folder': {
                #     'command': f'sudo mkdir -p {postgres_data_path}/wal_archive && \
                #         sudo chown postgres:postgres {postgres_data_path}/wal_archive',
                #     'signal set': 'Create wal folder',
                #     'signal get': ['Database created']
                # },

                'init db': {
                    'command': f'sudo su - postgres -c "pg_createcluster {VERSION_PG} contrprimer --datadir={postgres_data_path} --port={POSTGRES_PORT}"',
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
        provider.execute(vm_dates=VMS_DATES, commands=prepare,
                         vms_groups=VMS_GROUPS, username='u', password='1')

        sed_master_config = {
            'g_database': [

                # sssd.conf
                {
                    'path': f'/etc/sssd/sssd.conf',
                    'old': 'allowed_uids = 0, 33, 114, fly-dm, ipaapi',
                    'new': 'allowed_uids = 0, 33, 114, fly-dm, ipaapi, postgres'
                },
                # postgresql.conf
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#wal_level = replica			# minimal, replica, or logical',
                    'new': 'wal_level = replica'
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#archive_mode = off		# enables archiving; off, on, or always',
                    'new': 'archive_mode = on'
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': "#archive_command = ''		# command to use to archive a logfile segment",
                    'new': f"archive_command = 'cp %p /var/lib/postgresql/{VERSION_PG}/contrprimer/wal_archive/%f'"
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#max_wal_senders = 10		# max number of walsender processes',
                    'new': 'max_wal_senders = 10'
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#wal_keep_size = 0		# in megabytes; 0 disables',
                    'new': 'wal_keep_size = 128MB'
                },
                {
                    'path': f'{postgres_config_path}/postgresql.conf',
                    'old': '#hot_standby = on			# "off" disallows queries during recovery',
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
                    'old': f'local   replication     all                                     peer',
                    'new': 'local   replication     postgres                                trust'
                },
                {
                    'path': f'{postgres_config_path}/pg_hba.conf',
                    'old': f'host    replication     all             127.0.0.1/32            {pg_hba_proto}',
                    'new': 'host    replication     postgres        0.0.0.0/0               trust'
                },
            ]
        }
        provider.sed(sed_master_config, VMS_DATES, VMS_GROUPS)

        pg_ident = {
            'g_database': {
                'ident user0': {
                    'command': f'echo \'freeipa_map        user0@{DOMAIN}               user0\' | sudo tee -a {postgres_config_path}/pg_ident.conf',
                    'signal set': '',
                    'signal get': ''
                },
                'ident user1': {
                    'command': f'echo \'freeipa_map        user1@{DOMAIN}               user1\' | sudo tee -a {postgres_config_path}/pg_ident.conf',
                    'signal set': '',
                    'signal get': ''
                },
                'ident user2': {
                    'command': f'echo \'freeipa_map        user2@{DOMAIN}               user2\' | sudo tee -a {postgres_config_path}/pg_ident.conf',
                    'signal set': '',
                    'signal get': ''
                },
                'ident user3': {
                    'command': f'echo \'freeipa_map        user3@{DOMAIN}               user3\' | sudo tee -a {postgres_config_path}/pg_ident.conf',
                    'signal set': '',
                    'signal get': ''
                },
                'restart sssd': {
                    'command': f'sudo systemctl restart sssd',
                    'signal set': '',
                    'signal get': ''
                },

            }
        }
        provider.execute(VMS_DATES, pg_ident, VMS_GROUPS)

        start_cluster = {
            'database1': {
                'start db': {
                    'command': f'sudo systemctl enable postgresql@{VERSION_PG}-contrprimer && \
                            sudo systemctl start postgresql@{VERSION_PG}-contrprimer',
                    'signal set': 'Start maser',
                    'signal get': ''
                },

            },

            'g_replica': {
                'replication': {
                    'command': f'sudo su - postgres -c "pg_basebackup -h {VMS_DATES['database1']['ip_bridge']} -p {POSTGRES_PORT} -U postgres -D {postgres_data_path} -Fp -Xs -P -R"',
                    'signal set': 'Replication success',
                    'signal get': ['database1', 'Start maser']
                },
                'start replica': {
                    'command': f'sudo systemctl enable postgresql@{VERSION_PG}-contrprimer && \
                            sudo systemctl start postgresql@{VERSION_PG}-contrprimer',
                    'signal set': '',
                    'signal get': ['Replication success']
                }
            },

        }
        provider.execute(vm_dates=VMS_DATES, commands=start_cluster,
                         vms_groups=VMS_GROUPS, username='u', password='1')

        scp_sql = { # TODO переписать путь
            'database1': {
                'mode': 'push',
                'path_host': './roles/database/template/contrprimer.sql',
                'path_vm': '/tmp/contrprimer.sql'
            }
        }
        provider.scp(scp_sql, VMS_DATES)

        filling_bd = {
            'database1': {
                'create db': {
                    'command': f'sudo su - postgres -c "psql -p {POSTGRES_PORT} -c \'CREATE DATABASE contrprimer;\'"',
                    'signal set': 'CreateDB',
                    'signal get': ''
                },
                'create user': {
                    'command': f'sudo su - postgres -c "psql -p {POSTGRES_PORT} -c \'CREATE ROLE user0 LOGIN; CREATE ROLE user1 LOGIN; CREATE ROLE user2 LOGIN; CREATE ROLE user3 LOGIN;\'"',
                    'signal set': 'CreateDB user',
                    'signal get': ['CreateDB']
                },
                'filling db': {
                    'command': f'sudo su - postgres -c "psql -p {POSTGRES_PORT} -f /tmp/contrprimer.sql"',
                    'signal set': '',
                    'signal get': ['CreateDB user']
                }
            },
        }

        provider.execute(VMS_DATES, filling_bd)
