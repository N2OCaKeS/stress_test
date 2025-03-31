from allta import VBoxManager
from roles.vm_info import VERSION_PG, VMS_DATES, VMS_GROUPS


class DatabaseVM(): # TODO НАДО ПРОВЕРИТЬ!

    def __init__(self):
        self.provider = VBoxManager()

    def settings(self):
        """Настройка БД + репликация"""
        provider = self.provider
        postgres_config_path = f'/etc/postgresql/{VERSION_PG}/contrprimer'
        postgres_data_path = f'/var/lib/postgresql/{VERSION_PG}/contrprimer'
        log = '/tmp/contrprimer'
        unit_file = f"""sudo tee /etc/systemd/system/postgresql@{VERSION_PG}-contrprimer.service > /dev/null <<EOF
        [Unit]
        Description=PostgreSQL Cluster contrprimer {VERSION_PG}
        After=network.target

        [Service]
        Type=forking
        User=postgres
        Group=postgres
        Environment=PGDATA={postgres_config_path}
        ExecStart=/usr/lib/postgresql/{VERSION_PG}/bin/pg_ctl start -D ${{PGDATA}} -s -l ${{PGDATA}}/logfile
        ExecStop=/usr/lib/postgresql/{VERSION_PG}/bin/pg_ctl stop -D ${{PGDATA}} -s -m fast
        ExecReload=/usr/lib/postgresql/{VERSION_PG}/bin/pg_ctl reload -D ${{PGDATA}} -s

        [Install]
        WantedBy=multi-user.target
        EOF"""


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
                    'command': f'sudo su - postgres -c "pg_dropcluster {VERSION_PG} main --stop"',
                    'signal set': '',
                    'signal get': ['g_database' ,'Postgres privilege']
                }
            },
            'database1': {
                'create folder and change owner to postgres': {
                    'command': f'sudo mkdir {postgres_config_path} {postgres_data_path} && \
                        sudo chown postgres:postgres {postgres_config_path} {postgres_data_path}',
                    'signal set': 'Database 1: Created db path',
                    'signal get': ['database1' ,'Postgres privilege']
                },
                'create wal folder': {
                    'command': f'sudo mkdir -p {postgres_data_path}/wal_archive && \
                        sudo chown postgres:postgres {postgres_data_path}/contrprimer/wal_archive',
                    'signal set': '',
                    'signal get': ''
                },
                'create log file': {
                    'command': f'sudo chown postgres:postgres {postgres_config_path} && \
                        sudo touch {postgres_config_path}/logfile && \
                        sudo chown postgres:postgres {postgres_config_path}/logfile',
                    'signal set': '',
                    'signal get': ['database1', 'Database created']
                },
                'init db': {
                    'command': f'sudo su - postgres -c "pg_createcluster {VERSION_PG} contrprimer --datadir={postgres_data_path} --port=5440"',
                    'signal set': 'Database created',
                    'signal get': ['database1', 'Database 1: Created db path']
                },
            }
        }
        provider.execute(vm_dates=VMS_DATES, commands=prepare, vms_groups=VMS_GROUPS, username='u', password='1')

        sed_master_config = {  # TODO Написать конфигурацию

            # postgresql.conf
            # 'database1': {
            #     'path': f'{postgres_config_path}/postgresql.conf',
            #     'old': '#port = 5432				# (change requires restart)',
            #     'new': 'port = 5440'
            # },
            'database1': {
                'path': f'{postgres_config_path}/postgresql.conf',
                'old': '#wal_level = replica			# minimal, replica, or logical',
                'new': 'wal_level = hot_standby'
            },
            'database1': {
                'path': f'{postgres_config_path}/postgresql.conf',
                'old': '#archive_mode = off		# enables archiving; off, on, or always',
                'new': 'archive_mode = on'
            },
            'database1': {
                'path': f'{postgres_config_path}/postgresql.conf',
                'old': '#archive_command = ''		# command to use to archive a logfile segment',
                'new': f"archive_command = 'cp %p /var/lib/postgresql/{VERSION_PG}/contrprimer/wal_archive/%f'"
            },
            'database1': {
                'path': f'{postgres_config_path}/postgresql.conf',
                'old': '#max_wal_senders = 10		# max number of walsender processes',
                'new': 'max_wal_senders = 10'
            },
            'database1': {
                'path': f'{postgres_config_path}/postgresql.conf',
                'old': '#wal_keep_size = 0		# in megabytes; 0 disables',
                'new': 'wal_keep_size = 128MB'
            },
            'database1': {
                'path': f'{postgres_config_path}/postgresql.conf',
                'old': '#hot_standby = on			# "off" disallows queries during recovery',
                'new': 'hot_standby = on'
            },

            # pg_hba.conf
            'database1': {
                'path': f'{postgres_config_path}/pg_hba.conf',
                'old': 'local   all             postgres                                peer',
                'new': 'local   all             postgres                                trust'
            },
            'database1': {
                'path': f'{postgres_config_path}/pg_hba.conf',
                'old': 'local   all             all                                     peer',
                'new': 'local   all             all                                     trust'
            },
            'database1': {
                'path': f'{postgres_config_path}/pg_hba.conf',
                'old': 'host    all             all             0.0.0.0/0            scram-sha-256',
                'new': 'host    all             all             0.0.0.0/0            trust'
            },
            'database1': {
                'path': f'{postgres_config_path}/pg_hba.conf',
                'old': 'host    all             all             ::1/128                 scram-sha-256',
                'new': 'host    all             all             ::1/128                 trust'
            },
            'database1': {
                'path': f'{postgres_config_path}/pg_hba.conf',
                'old': 'local   replication     all                                     peer',
                'new': 'local   replication     postgres                                trust'
            },
            'database1': {
                'path': f'{postgres_config_path}/pg_hba.conf',
                'old': 'host    replication     all             127.0.0.1/32            scram-sha-256',
                'new': 'host    replication     postgres        0.0.0.0/0               trust'
            },
        }
        # provider.sed(sed_master_config, VMS_DATES, VMS_GROUPS)
        start_cluster = {
            'database1': {
                'start db': {
                    'command': f'sudo su - postgres -c "/usr/lib/postgresql/{VERSION_PG}/bin/pg_ctl -D {postgres_config_path} -l {log} start',
                    'signal set': 'Start cluster',
                    'signal get': ''
                },
            },
            'g_replica': {
                'replication': {
                    'command': f'sudo su - postgres -c "pg_basebackup -h {VMS_DATES['database1']['ip']} -p 5440 -U postgres -D {postgres_config_path} -Fp -Xs -P -R"',
                    'signal set': '',
                    'signal get': 'Start cluster'
                },
            },
        }
        # provider.execute(vm_dates=VMS_DATES, commands=start_cluster, vms_groups=VMS_GROUPS, username='u', password='1')

        sed_replica_config = {
            'g_replica': {
                'path': f'{postgres_config_path}/postgresql.conf',
                'old': '#port = 5432				# (change requires restart)',
                'new': 'port = 5440'
            },
            'g_replica': {
                'path': f'{postgres_config_path}/postgresql.conf',
                'old': '#hot_standby = on			# "off" disallows queries during recovery',
                'new': 'hot_standby = on'
            },
        }
        # provider.sed(sed_replica_config, VMS_DATES, VMS_GROUPS)

        start_bd = {
            'g_database':{
                'start db':{
                    'command':f'sudo systemctl daemon-reexec && \
                            sudo systemctl daemon-reload && \
                            sudo systemctl enable postgresql@{VERSION_PG}-contrprimer && \
                            sudo systemctl start postgresql@{VERSION_PG}-contrprimer',
                    'signal set': '',
                    'signal get': ''
                }
            }
        }
        # provider.execute(vm_dates=VMS_DATES, commands=start_bd, vms_groups=VMS_GROUPS, username='u', password='1')


# unit_file = "[Unit]\n \
#             Description=PostgreSQL Cluster contrprimer 15 \n \
#             After=network.target\n\n \
#             [Service] \n \
#             Type=forking\n \
#             User=postgres\n \
#             Group=postgres\n \
#             Environment=PGDATA=/etc/postgresql/15/contrprimer\n \
#             ExecStart=/usr/lib/postgresql/15/bin/pg_ctl start -D ${PGDATA} -s -l ${PGDATA}/logfile\n \
#             ExecStop=/usr/lib/postgresql/15/bin/pg_ctl stop -D ${PGDATA} -s -m fast\n \
#             ExecReload=/usr/lib/postgresql/15/bin/pg_ctl reload -D ${PGDATA} -s\n\n \
#             [Install]\n \
#             WantedBy=multi-user.target\n \
#             "
