import re
import os
import json
import logging
import subprocess
from typing import LiteralString
import time
import asyncio
import psycopg
import psycopg.sql
from psycopg_pool import AsyncConnectionPool
from allta import MathModel
import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt


from psb_conf import LOG_FILENAME, DATABASE_NAME, \
    MAC_SQL_UPGRADE, MAC_SQL_TRANSACTION, \
    TABLESPACE_DEFAULT, REPORT_FILENAME, REPORT_PATH, PG_SETEST_CLUSTER, PG_SETEST_PORT, PG_VERSION, PG_VERSION_18
from libs.libpsb import init_test_tables, upgrade_test_table, pgbench, pgbench_custom, astra_version
# import pysnooper


class Test:
    def __init__(self,
                 database=DATABASE_NAME,
                 tablespace=TABLESPACE_DEFAULT,
                 cluster_port=PG_SETEST_PORT,
                 scale=1,
                 filling=100,
                 ths=10,
                 trs=10,
                 cls=1,
                 mac_sql_trn=MAC_SQL_TRANSACTION,
                 debian=False,
                 parsec=False,
                 tantor=False):

        logging.basicConfig(filename=LOG_FILENAME,
                            filemode="a+",
                            level=logging.INFO,
                            format='%(levelname)s: t:%(created)f th:%(thread)d ps:%(process)d <%(name)s> | %(message)s')
        self.logger = logging.getLogger()
        self.db = database
        self.port = cluster_port
        self.tspace = tablespace
        self.scale_factor = scale
        self.filling_factor = filling
        self.threads = ths
        self.transactions = trs
        self.clients = cls
        self.mac_sql_script = mac_sql_trn
        self.debian = debian
        self.parsec = parsec
        self.tantor = tantor
        # self.pgbench_cmd_deb = f"su -c 'pgbench --random-seed=13 -t {self.transactions} -j {self.threads} -c {self.clients} {self.db}' postgres"
        # self.pgbench_cmd = f"su -c 'pgbench -h localhost -p {self.port} --random-seed=13 -t {self.transactions} -j {self.threads} -c {self.clients} \
        #                     {self.db}' postgres"
        # self.pgbench_cmd_custom = f"su -c 'pgbench -h localhost -p {self.port} --random-seed=13 -t {self.transactions} -j {self.threads} -c {self.clients} \
        #                             -f {self.mac_sql_script}@2 {self.db}' postgres"
        # self.pgbench_cmd_parsec = f"su -c 'pgbench -h localhost --macs -p {self.port} --random-seed=13 -t {self.transactions} \
        #                             -j {self.threads} -c {self.clients} test_parsec' u_1"
        self.pgbench_cmd_deb = f'pgbench --random-seed=13 -U postgres -t {self.transactions} -j {self.threads} -c {self.clients} {self.db}'
        self.pgbench_cmd = f'pgbench -h localhost -p {self.port} -U postgres --random-seed=13 -t {self.transactions} -j {self.threads} -c {self.clients} {self.db}'
        self.pgbench_cmd_custom = f'pgbench -h localhost -p {self.port} -U postgres --random-seed=13 -t {self.transactions} -j {self.threads} -c {self.clients} \
                                    -f {self.mac_sql_script}@2 {self.db}'
        self.pgbench_cmd_parsec = f'pgbench -h localhost --macs=fixed -p {self.port} -U u_1 --random-seed=13 -t {self.transactions} \
                                    -j {self.threads} -c {self.clients} test_parsec'
        self.pgbench_tantor_cmd = f"/opt/tantor/db/15/bin/pgbench -h localhost -p 5432 -U postgres --random-seed=13 -t {self.transactions} \
                                     -j {self.threads} -c {self.clients} test_parsec"

    #@pysnooper.snoop()
    def run_test(self):
        '''
            Запуск проверки на встроенных тестовых скриптах
            tpcb-like simple-update и select-only
            Работает.
        '''
        if self.debian == True:
            init_test_tables(self.db, self.tspace, self.port, self.scale_factor, self.filling_factor, self.debian)
        elif self.parsec == True:
            init_test_tables(self.db, self.tspace, self.port, self.scale_factor, self.filling_factor, parsec=self.parsec)
        elif self.tantor == True:
            init_test_tables(self.db, self.tspace, self.port, self.scale_factor, self.filling_factor, tantor=self.tantor)
        else:
            init_test_tables(self.db, self.tspace, self.port, self.scale_factor, self.filling_factor)
        result = '# TEST # --- '
        try:
            if self.debian == True:
                decode_std = pgbench(self.pgbench_cmd_deb)
            elif self.parsec == True:
                decode_std = pgbench(self.pgbench_cmd_parsec)
            elif self.tantor == True:
                decode_std = pgbench(self.pgbench_tantor_cmd)
            else:
                decode_std = pgbench(self.pgbench_cmd)
            out = os.linesep.join([s for s in decode_std[0].splitlines() if s])
            print(out)
            err = os.linesep.join([s for s in decode_std[1].splitlines() if s])
            print(err)

            al_version = astra_version()[0] 
            if str(al_version).startswith('1.8'):
                psql_version = PG_VERSION_18
            elif str(al_version).startswith('1.7'):
                psql_version = PG_VERSION
            else: psql_version = PG_VERSION

            if psql_version == 15:
                latency_average = re.findall(r'latency\saverage\s=\s(\d+\.\d+)', out)[0]
            elif self.tantor == True:
                latency_average = re.findall(r'(\d+\.\d+)', out)[2]
            elif psql_version == 11:
                latency_average = re.search(r'(\d+\.\d+)', out).group(1)
            else:
                latency_average = re.findall(r'(\d+\.\d+)', out)[2]
            
            completed_transactions = re.search(r'(\d+)/', out).group(1)
            expected_transactions = re.search(r'/(\d+)', out).group(1)
            tps_including_connections_establishing = re.findall(r'tps\s=\s(\d+\.\d+)', out)[0]
            try:
                tps_excluding_connections_establishing = re.findall(r'tps\s=\s(\d+\.\d+)', out)[1]
            except IndexError:
                tps_excluding_connections_establishing = re.findall(r'tps\s=\s(\d+\.\d+)', out)[0]

            result += '{la} {tps1}|{tps2} '.format(la=latency_average+' ms',
                                                   tps1=tps_including_connections_establishing,
                                                   tps2=tps_excluding_connections_establishing)
            if completed_transactions == expected_transactions:
                result += '--- \033[92mpass\033[0m'
            else:
                result += '--- \033[91mfail\033[0m'

            # in file
            with open(REPORT_FILENAME, 'a+') as report_file:
                report_file.write(' {} {} {} {} {}\n'.format(latency_average,
                                                             tps_including_connections_establishing,
                                                             tps_excluding_connections_establishing,
                                                             completed_transactions,
                                                             expected_transactions))

        except Exception as exception:
            with open(REPORT_FILENAME, 'a+') as report_file:
                report_file.write(' 0 0 0 0 0\n')
            print('\nТестирование завершилось исключением:')
            print(f'Type: {type(exception).__name__}, Message: {str(exception)}')
            return False
        return result

    def run_test_custom(self, upgrade_script=MAC_SQL_UPGRADE, test_script=MAC_SQL_TRANSACTION):
        '''
            Запуск pgbench c указанием скрипта постнастройки и скрипта транзакций
            TODO:
                Проработать тестовый скрипт MAC_SQL_TRANSACTION
                Проработать тестовый скрипт MIC_SQL_TRANSACTION
                Проработать тестовый скрипт ACL_SQL_TRANSACTION
        '''
        init_test_tables(self.db, self.tspace, self.scale_factor, self.filling_factor)
        upgrade_test_table(upgrade_script)
        result = '# TEST # --- '
        try:
            decode_std = pgbench_custom(test_script, self.pgbench_cmd_custom)
            out = os.linesep.join([s for s in decode_std[0].splitlines() if s])
            self.logger.info(out)
            err = os.linesep.join([s for s in decode_std[1].splitlines() if s])
            self.logger.error(err)

            result += '{la} {tps1}|{tps2} '.format(la=re.search(r'(\d+\.\d+ ms)', out).group(1),
                                                   tps1=re.findall(r'tps\s=\s(\d+\.\d+)', out)[0],
                                                   tps2=re.findall(r'tps\s=\s(\d+\.\d+)', out)[1])
            if re.search(r'(\d+)/', out).group(1) == re.search(r'/(\d+)', out).group(1):
                result += '--- \033[92mpass\033[0m'
            else:
                result += '--- \033[91mfail\033[0m'

        except Exception as exception:
            self.logger.error('Тестирование завершилось исключением:\n')
            self.logger.error(exception)
            return False
        return result



class OLAPTest:

    def __init__(self):
        self.db_config = {
            "host": "127.0.0.1",
            "port": 6000,
            "dbname": "protopack",
            "user": "postgres",
            "password": "12345678",
        }
        self.passes = 5

        self.hard_query = """
SELECT
    bp.build,
    bp.source,
    bp."binary",
    bp.binary_version,
    bp.create_utc,
    ps.description
FROM main.build_packages AS bp
JOIN main.build_sources AS ps
    ON ps.build = bp.build
    AND ps.source = bp.source
WHERE
        bp."binary" ILIKE '%lib%'
    OR bp.source ILIKE '%lib%'
    OR COALESCE(bp.binary_info, '') ILIKE '%lib%'
    OR COALESCE(bp.files, '') ILIKE '%lib%'
    OR COALESCE(ps.description, '') ILIKE '%lib%'
ORDER BY
    md5(
        COALESCE(bp.source, '') ||
        COALESCE(bp."binary", '') ||
        COALESCE(bp.binary_version, '') ||
        COALESCE(bp.binary_info, '') ||
        COALESCE(bp.files, '') ||
        COALESCE(ps.description, '')
    ),
    bp.create_utc DESC
""" # 27 сек
        self.order_query = """
SELECT *
FROM main.build_packages
ORDER BY
    md5(
        COALESCE(source, '') ||
        COALESCE("binary", '') ||
        COALESCE(binary_version, '') ||
        COALESCE(source_version, '') ||
        COALESCE(binary_info, '') ||
        COALESCE(files, '') ||
        COALESCE(task::text, '') ||
        COALESCE(fb::text, '') ||
        COALESCE(fn::text, '') ||
        COALESCE(depends::text, '') ||
        COALESCE(binary_info_json::text, '')
    ),
    length(COALESCE(files, '')) DESC,
    create_utc DESC
""" # 41 секунда
        self.substring_search_query = """
SELECT *
FROM main.build_packages
WHERE "binary" ILIKE '%lib%'
""" # 12 секунд
        self.join_query = """
SELECT
    ps.build,
    ps.source,
    ps.source_version,
    ps.description,
    ps.repository,
    bp.repository AS bp_repository,
    bp.component,
    bp."binary",
    bp.binary_version,
    bp.section,
    bp.responsible,
    bp.create_utc
FROM main.build_sources AS ps
JOIN main.build_packages AS bp
    ON ps.build = bp.build
    AND ps.source = bp.source
    AND ps.build = 'debian.sid.unstable'
""" # 4-5 секунд
        self.pool_connection_timeout = 600.0
        self.results_file = f"{REPORT_PATH}/olap_results.json"
        self.oom_db_name = "test"
        self.oom_query_name = "oom_form_am289n04_query"
        self.oom_query = "SELECT am289.form_am289n04()"
        self.oom_cluster_name = PG_SETEST_CLUSTER
        self.oom_cluster_port = self.db_config["port"]
        self.oom_roles = {
            "am289": "useram289",
            "data": "userdata",
            "data_db": "userdata_db",
            "data_common": "userdata_com",
            "ab122": "userab122",
            "ott1g": "userott1g",
            "ott32": "userott32",
        }
        self.oom_dump_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "sql", "test.tar.gz")
        )

        self.available_queries: dict[str, dict[str, object]] = {
            "hard_query": {
                "query": self.hard_query,
            },
            "order_query": {
                "query": self.order_query,
            },
            "substring_search_query": {
                "query": self.substring_search_query,
            },
            "join_query": {
                "query": self.join_query,
            },
            self.oom_query_name: {
                "query": self.oom_query,
                "db_config": {**self.db_config, "dbname": self.oom_db_name},
            },
        }

    def _get_cluster_version(self):
        al_version = astra_version()[0]
        if str(al_version).startswith("1.8"):
            return PG_VERSION_18
        return PG_VERSION

    def _run_cluster_command(self, args, check=True):
        process = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if check and process.returncode != 0:
            raise RuntimeError(
                "Cluster command failed: "
                + " ".join(str(arg) for arg in args)
                + os.linesep
                + process.stderr.strip()
            )
        return process

    def _run_psql_as_postgres(self, dbname="postgres", sql=None, stdin=None):
        # Используем -i (login shell / логин-сессия) чтобы PAM загрузил Parsec-атрибуты
        # пользователя postgres, включая привилегию "change MAC label"
        # We use -i (login shell) so PAM loads Parsec attributes for the postgres user,
        # including the "change MAC label" privilege
        command = [
            "sudo",
            "-i",
            "-u",
            "postgres",
            "psql",
            "-p",
            str(self.oom_cluster_port),
            "-d",
            dbname,
            "-v",
            "ON_ERROR_STOP=1",
        ]
        if sql is not None:
            command.extend(["-c", sql])

        process = subprocess.run(
            command,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            raise RuntimeError(
                "psql command failed: "
                + " ".join(command)
                + os.linesep
                + process.stderr.strip()
            )
        return process

    def _create_oom_system_users(self):
        # Создаём системных пользователей с MAC-метками как в psb_large_tmp_table_prep.sh
        # Create system users with MAC labels as in psb_large_tmp_table_prep.sh
        users_label_02 = ["am289", "data", "ab122", "ott1g", "ott32"]
        users_label_00 = ["data_db", "data_common"]

        for username in users_label_02:
            self._run_cluster_command(
                ["adduser", "--disabled-password", "--gecos", "", username],
                check=False,
            )
            self._run_cluster_command(["pdpl-user", "-l", "0:2", username], check=False)

        for username in users_label_00:
            self._run_cluster_command(
                ["adduser", "--disabled-password", "--gecos", "", username],
                check=False,
            )
            self._run_cluster_command(["pdpl-user", "-l", "0:0", username], check=False)

    def _reinstall_postgresql(self, cluster_version: str):
        # Полностью удаляем PostgreSQL и все данные, затем устанавливаем заново.
        # Completely remove PostgreSQL and all data, then reinstall from scratch.
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"

        print(f"Останавливаю PostgreSQL...")
        # Останавливаем сервис перед удалением / Stop service before removal
        self._run_cluster_command(["systemctl", "stop", "postgresql"], check=False)

        print(f"Удаляю PostgreSQL {cluster_version} и все данные...")
        # Удаляем пакеты / Remove packages
        subprocess.run(
            [
                "apt-get", "purge", "--auto-remove", "-y",
                f"postgresql-{cluster_version}",
                "postgresql-common",
                "postgresql-client-common",
            ],
            env=env,
            check=False,
        )

        # Удаляем оставшиеся данные, конфиги и systemd-директории / Remove remaining data, configs and systemd dirs
        subprocess.run(["rm", "-rf", "/var/lib/postgresql/"], check=False)
        subprocess.run(["rm", "-rf", "/etc/postgresql/"], check=False)
        # Удаляем systemd service drop-in директории, иначе pg_createcluster упадёт с ошибкой "File exists"
        # Remove systemd service drop-in dirs, otherwise pg_createcluster fails with "File exists"
        subprocess.run(
            ["bash", "-c", f"rm -rf /etc/systemd/system/postgresql@{cluster_version}-*.service.d"],
            check=False,
        )
        subprocess.run(["systemctl", "daemon-reload"], check=False)

        print(f"Устанавливаю PostgreSQL {cluster_version}...")
        # Устанавливаем заново / Reinstall
        result = subprocess.run(
            ["apt-get", "install", "-y", f"postgresql-{cluster_version}"],
            env=env,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Не удалось установить postgresql-{cluster_version}"
            )

    def _recreate_oom_cluster(self):
        cluster_version = str(self._get_cluster_version())
        cluster_name = self.oom_cluster_name
        cluster_port = str(self.oom_cluster_port)
        cluster_conf_dir = f"/etc/postgresql/{cluster_version}/{cluster_name}"
        hba_path = os.path.join(cluster_conf_dir, "pg_hba.conf")
        postgresql_conf_path = os.path.join(cluster_conf_dir, "postgresql.conf")

        print(
            f"Пересоздаю кластер {cluster_name} "
            f"(PostgreSQL {cluster_version}, порт {cluster_port})"
        )

        # Создаём системных пользователей с MAC-метками (как в psb_large_tmp_table_prep.sh)
        # Create system users with MAC labels (as in psb_large_tmp_table_prep.sh)
        self._create_oom_system_users()

        # Полностью удаляем PostgreSQL и устанавливаем заново (как в OOM vagrant-скрипте)
        # Completely remove PostgreSQL and reinstall (as in OOM vagrant script)
        self._reinstall_postgresql(cluster_version)

        # После установки apt создаёт кластер main — удаляем его
        # After install, apt creates a main cluster — drop it
        self._run_cluster_command(
            ["pg_ctlcluster", cluster_version, "main", "stop"],
            check=False,
        )
        self._run_cluster_command(
            ["pg_dropcluster", cluster_version, "main", "--stop"],
            check=False,
        )

        # Создаём нужный кластер на нужном порту / Create the required cluster on the required port
        self._run_cluster_command(
            ["pg_createcluster", cluster_version, cluster_name, "--port", cluster_port]
        )

        if os.path.isfile(hba_path):
            with open(hba_path, "r", encoding="utf-8") as hba_stream:
                hba_content = hba_stream.read()
            hba_content = (
                hba_content
                .replace("scram-sha-256", "trust")
                .replace("md5", "trust")
                .replace("peer", "trust")
            )
            with open(hba_path, "w", encoding="utf-8") as hba_stream:
                hba_stream.write(hba_content)

        if os.path.isfile(postgresql_conf_path):
            with open(postgresql_conf_path, "r", encoding="utf-8") as conf_stream:
                conf_lines = conf_stream.readlines()

            updated_conf_lines = []
            ignore_socket_configured = False
            grant_options_configured = False
            for line in conf_lines:
                if re.match(r"\s*#?\s*ac_ignore_socket_maclabel\s*=", line):
                    updated_conf_lines.append(
                        "ac_ignore_socket_maclabel = false\n"
                    )
                    ignore_socket_configured = True
                elif re.match(r"\s*#?\s*ac_enable_grant_options\s*=", line):
                    updated_conf_lines.append(
                        "ac_enable_grant_options = true\n"
                    )
                    grant_options_configured = True
                else:
                    updated_conf_lines.append(line)

            if not ignore_socket_configured:
                updated_conf_lines.append(
                    "\nac_ignore_socket_maclabel = false\n"
                )
            if not grant_options_configured:
                updated_conf_lines.append(
                    "ac_enable_grant_options = true\n"
                )

            with open(postgresql_conf_path, "w", encoding="utf-8") as conf_stream:
                conf_stream.writelines(updated_conf_lines)

        self._run_cluster_command(["pdpl-user", "-i", "63", "postgres"])
        self._run_cluster_command(
            ["usermod", "-a", "-G", "shadow", "postgres"],
            check=False,
        )
        self._run_cluster_command(
            ["setfacl", "-d", "-m", "u:postgres:r", "/etc/parsec/macdb"]
        )
        self._run_cluster_command(
            ["setfacl", "-R", "-m", "u:postgres:r", "/etc/parsec/macdb"]
        )
        self._run_cluster_command(
            ["setfacl", "-m", "u:postgres:rx", "/etc/parsec/macdb"]
        )
        self._run_cluster_command(
            ["setfacl", "-d", "-m", "u:postgres:r", "/etc/parsec/capdb"]
        )
        self._run_cluster_command(
            ["setfacl", "-R", "-m", "u:postgres:r", "/etc/parsec/capdb"]
        )
        self._run_cluster_command(
            ["setfacl", "-m", "u:postgres:rx", "/etc/parsec/capdb"]
        )
        self._run_cluster_command(
            ["systemctl", "restart", "parsec"]
        )
        # Полный перезапуск через systemd (как в psb_large_tmp_table_prep.sh) корректно
        # инициализирует Parsec-контекст процесса postgres, включая "change MAC label"
        # Full restart via systemd (as in psb_large_tmp_table_prep.sh) correctly
        # initializes the Parsec context for the postgres process, including "change MAC label"
        self._run_cluster_command(
            ["systemctl", "restart", "postgresql"]
        )

    def _prepare_oom_roles_and_labels(self):
        self._run_psql_as_postgres(sql="MAC LABEL ON CLUSTER IS '{2,0}';")
        self._run_psql_as_postgres(sql="MAC LABEL ON TABLESPACE pg_global IS '{2,0}';")
        self._run_psql_as_postgres(sql="MAC CCR ON CLUSTER IS OFF;")

        for role_name, role_password in self.oom_roles.items():
            self._run_psql_as_postgres(
                sql=f"CREATE USER {role_name} WITH PASSWORD '{role_password}';"
            )

        self._run_psql_as_postgres(sql=f"CREATE DATABASE {self.oom_db_name};")
        self._run_psql_as_postgres(
            dbname=self.oom_db_name,
            sql=f"MAC LABEL ON DATABASE {self.oom_db_name} IS '{{2,0}}';",
        )
        self._run_psql_as_postgres(
            dbname=self.oom_db_name,
            sql=f"MAC CCR ON DATABASE {self.oom_db_name} IS OFF;",
        )
        self._run_psql_as_postgres(
            dbname=self.oom_db_name,
            sql="MAC CCR ON SCHEMA public IS OFF;",
        )
        self._run_psql_as_postgres(
            dbname=self.oom_db_name,
            sql="MAC LABEL ON SCHEMA public IS '{2,0}';",
        )

    def _restore_oom_database(self):
        if not os.path.isfile(self.oom_dump_path):
            raise FileNotFoundError(
                f"OOM dump not found: {self.oom_dump_path}"
            )

        admin_config = dict(self.db_config)
        admin_config["dbname"] = "postgres"

        with psycopg.connect(
            host=admin_config["host"],
            port=admin_config["port"],
            dbname=admin_config["dbname"],
            user=admin_config["user"],
            password=admin_config["password"],
            autocommit=True,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = %s AND pid <> pg_backend_pid()
                    """,
                    (self.oom_db_name,),
                )
                cur.execute(
                    psycopg.sql.SQL("DROP DATABASE IF EXISTS {}").format(
                        psycopg.sql.Identifier(self.oom_db_name)
                    )
                )
                for role_name in self.oom_roles:
                    cur.execute(
                        psycopg.sql.SQL("DROP ROLE IF EXISTS {}").format(
                            psycopg.sql.Identifier(role_name)
                        )
                    )

        self._prepare_oom_roles_and_labels()

        env = os.environ.copy()
        env["PGPASSWORD"] = str(self.db_config["password"])

        tar_process = subprocess.Popen(
            ["tar", "-xOf", self.oom_dump_path, "test.sql"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # Дамп содержит CREATE USER для уже существующих ролей — не используем ON_ERROR_STOP,
            # как в оригинальном bash-скрипте psb_large_tmp_table_prep.sh
            # The dump contains CREATE USER for already existing roles — no ON_ERROR_STOP,
            # same as in original bash script psb_large_tmp_table_prep.sh
            restore_process = subprocess.run(
                [
                    "sudo",
                    "-i",
                    "-u",
                    "postgres",
                    "psql",
                    "-p", str(self.db_config["port"]),
                    "-d", self.oom_db_name,
                ],
                stdin=tar_process.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        finally:
            if tar_process.stdout is not None:
                tar_process.stdout.close()

        tar_stderr = ""
        if tar_process.stderr is not None:
            tar_stderr = tar_process.stderr.read()
        tar_return_code = tar_process.wait()

        if restore_process.returncode != 0:
            raise RuntimeError(
                "Failed to restore OOM database: "
                + restore_process.stderr
            )
        if tar_return_code not in (0, -13, 141):
            raise RuntimeError(
                "Failed to extract OOM dump: "
                + tar_stderr
            )

    @staticmethod
    def _read_proc_rss_kb(pid: int) -> float:
        # Читаем VmRSS — то же значение что htop показывает в колонке RES
        # RssAnon
        try:
            with open(f"/proc/{pid}/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return float(line.split()[1])  # значение в кБ / value in kB
        except (FileNotFoundError, ValueError, OSError):
            pass
        return 0.0

    def _run_oom_query_iterations(self) -> tuple[list[float], list[float]]:
        print("Восстанавливаю OOM базу данных test")
        self._restore_oom_database()
        iterations = []
        memory_iterations = []

        for pass_num in range(1, max(1, int(self.passes)) + 1):
            with psycopg.connect(
                host=self.db_config["host"],
                port=self.db_config["port"],
                dbname=self.oom_db_name,
                user=self.db_config["user"],
                password=self.db_config["password"],
            ) as conn:
                with conn.cursor() as cur:
                    # Получаем PID серверного процесса PostgreSQL / Get PID of PostgreSQL backend process
                    cur.execute("SELECT pg_backend_pid()")
                    pid_row = cur.fetchone()
                    assert pid_row is not None
                    backend_pid = pid_row[0]

                    rss_before = self._read_proc_rss_kb(backend_pid)
                    started = time.perf_counter()
                    cur.execute(self.oom_query)  # type: ignore[arg-type]
                    if cur.description is not None:
                        cur.fetchall()
                    measurement = time.perf_counter() - started
                    rss_after = self._read_proc_rss_kb(backend_pid)

            memory_mb = max(0.0, rss_after - rss_before) / 1024
            iterations.append(measurement)
            memory_iterations.append(memory_mb)
            print(
                f"Запрос {self.oom_query_name}, проход {pass_num}: "
                f"{measurement:.3f} сек, память: {memory_mb:.2f} МБ"
            )

        return iterations, memory_iterations

    async def run_test(self):
        db_config = self.db_config
        results_file_path = os.path.abspath(self.results_file)
        report_dir = os.path.dirname(results_file_path)

        dsn = (
            f"host={db_config['host']} "
            f"port={db_config['port']} "
            f"dbname={db_config['dbname']} "
            f"user={db_config['user']} "
            f"password={db_config['password']}"
        )

        passes = max(1, int(self.passes))
        prepared_queries = list(self.available_queries.items())

        def calc_percentile(values, percentile):
            if not values:
                return 0.0
            sorted_values = sorted(values)
            if len(sorted_values) == 1:
                return sorted_values[0]
            position = (len(sorted_values) - 1) * (percentile / 100)
            lower_index = int(position)
            upper_index = min(lower_index + 1, len(sorted_values) - 1)
            fraction = position - lower_index
            return (
                sorted_values[lower_index]
                + (sorted_values[upper_index] - sorted_values[lower_index]) * fraction
            )

        def calc_median(values):
            if not values:
                return 0.0
            sorted_values = sorted(values)
            mid = len(sorted_values) // 2
            if len(sorted_values) % 2 == 0:
                return (sorted_values[mid - 1] + sorted_values[mid]) / 2
            return sorted_values[mid]

        def save_speed_graph(query_name, iterations):
            graph_file_name = f"olap_speed_{query_name}.png"
            graph_file_path = os.path.abspath(os.path.join(report_dir, graph_file_name))
            if not iterations:
                return graph_file_path

            x_values = list(range(1, len(iterations) + 1))
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(x_values, iterations, marker="o", linewidth=1.2)
            ax.set_title(f"OLAP speed graph: {query_name}")
            ax.set_xlabel("Request order")
            ax.set_ylabel("Seconds")
            ax.grid(True, linestyle="--", alpha=0.4)
            fig.tight_layout()
            fig.savefig(graph_file_path, dpi=150)
            plt.close(fig)
            return graph_file_path

        def save_memory_graph(query_name, all_samples: list[list[float]]) -> str:
            # all_samples — список списков: каждый внутренний список это сэмплы памяти (МБ) одной итерации
            # all_samples — list of lists: each inner list is memory samples (MB) for one iteration
            graph_file_name = f"olap_memory_{query_name}.png"
            graph_file_path = os.path.abspath(os.path.join(report_dir, graph_file_name))
            if not all_samples or not any(all_samples):
                return graph_file_path

            # Tab10 цвета заданы явно чтобы не зависеть от версии matplotlib
            # Tab10 colors defined explicitly to avoid matplotlib version dependency
            colors = [
                "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
            ]
            fig, ax = plt.subplots(figsize=(12, 5))

            # Кривая каждой итерации своим цветом / Each iteration curve in its own color
            for idx, samples in enumerate(all_samples):
                if not samples:
                    continue
                x = list(range(len(samples)))
                ax.plot(x, samples,
                        color=colors[idx % len(colors)],
                        linewidth=1.2,
                        alpha=0.7,
                        label=f"Итерация {idx + 1}")

            # Медиана по каждой временной точке красным / Median at each time point in red
            max_len = max((len(s) for s in all_samples if s), default=0)
            median_values = []
            for t_idx in range(max_len):
                vals = [s[t_idx] for s in all_samples if len(s) > t_idx]
                if vals:
                    sorted_v = sorted(vals)
                    mid = len(sorted_v) // 2
                    m = (sorted_v[mid - 1] + sorted_v[mid]) / 2 if len(sorted_v) % 2 == 0 else sorted_v[mid]
                    median_values.append(m)
            if median_values:
                x_med = list(range(len(median_values)))
                ax.plot(x_med, median_values,
                        color="red",
                        linewidth=2.0,
                        linestyle="--",
                        label="Медиана")

            ax.set_title(f"Memory usage: {query_name}")
            ax.set_xlabel("Time (seconds)")
            ax.set_ylabel("Memory (MB)")
            ax.legend()
            ax.grid(True, linestyle="--", alpha=0.4)
            fig.tight_layout()
            fig.savefig(graph_file_path, dpi=150)
            plt.close(fig)
            return graph_file_path

        def build_result_entry(query_name, iterations, all_memory_samples=None, memory_graph=None):
            median_value = calc_median(iterations)
            p99_value = calc_percentile(iterations, 99)
            p95_value = calc_percentile(iterations, 95)
            p50_value = calc_percentile(iterations, 50)
            min_value = min(iterations) if iterations else 0.0
            max_value = max(iterations) if iterations else 0.0
            graph_file_path = save_speed_graph(query_name, iterations)
            entry = {
                "iterations": [round(value, 3) for value in iterations],
                "median": round(median_value, 3),
                "p99": round(p99_value, 3),
                "p95": round(p95_value, 3),
                "p50": round(p50_value, 3),
                "min": round(min_value, 3),
                "max": round(max_value, 3),
                "speed_graph": graph_file_path,
            }
            # Добавляем статистику по памяти если она передана / Add memory stats if provided
            # all_memory_samples — список списков сэмплов (МБ) по каждому проходу
            # all_memory_samples — list of sample lists (MB) per iteration
            if all_memory_samples:
                flat = [v for samples in all_memory_samples for v in samples]
                entry["memory_mb"] = {
                    "iterations": [[round(v, 2) for v in s] for s in all_memory_samples],
                    "median": round(calc_median(flat), 2),
                    "min": round(min(flat), 2),
                    "max": round(max(flat), 2),
                    "graph": memory_graph,
                }
            return entry

        pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=1,
            open=False,
        )

        os.makedirs(report_dir, exist_ok=True)

        # Если OOM-запрос есть в списке — пересоздаём кластер и восстанавливаем БД до открытия пула
        # If OOM query is in the list — recreate cluster and restore DB before opening the pool
        if self.oom_query_name in self.available_queries:
            self._recreate_oom_cluster()
            self._restore_oom_database()

        await pool.open()
        try:

            async def run_one(
                query_text: str,
                db_cfg: dict | None = None,
            ) -> tuple[float, dict[str, float], list[float]]:
                samples: list[float] = []
                stop_event = asyncio.Event()

                async def sample_loop(pid: int) -> None:
                    # Собираем VmRSS каждую секунду пока выполняется запрос
                    # Collect VmRSS every second while the query runs
                    while not stop_event.is_set():
                        samples.append(self._read_proc_rss_kb(pid) / 1024)
                        await asyncio.sleep(1.0)

                async def execute(conn) -> float:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT pg_backend_pid()")
                        pid_row = await cur.fetchone()
                        assert pid_row is not None
                        sample_task = asyncio.create_task(sample_loop(pid_row[0]))
                        try:
                            started = time.perf_counter()
                            await cur.execute(query_text)  # type: ignore[arg-type]
                            await cur.fetchall()
                            return time.perf_counter() - started
                        finally:
                            stop_event.set()
                            await sample_task

                if db_cfg is not None:
                    # Прямое подключение для запросов с другой БД / Direct connection for different DB
                    async with await psycopg.AsyncConnection.connect(**db_cfg) as conn:
                        elapsed = await execute(conn)
                else:
                    async with pool.connection(timeout=self.pool_connection_timeout) as conn:
                        elapsed = await execute(conn)

                if not samples:
                    memory_stats: dict[str, float] = {"median": 0.0, "min": 0.0, "max": 0.0}
                else:
                    sorted_s = sorted(samples)
                    mid = len(sorted_s) // 2
                    median = (
                        (sorted_s[mid - 1] + sorted_s[mid]) / 2
                        if len(sorted_s) % 2 == 0
                        else sorted_s[mid]
                    )
                    memory_stats = {
                        "median": round(median, 2),
                        "min": round(sorted_s[0], 2),
                        "max": round(sorted_s[-1], 2),
                    }

                return elapsed, memory_stats, samples

            result = {
                "time_unit": "seconds",
                "result": {},
            }
            total_rating_data: dict[str, list[float]] = {}

            for query_name, query_cfg in prepared_queries:
                query_text = query_cfg.get("query")
                if not isinstance(query_text, str):
                    raise ValueError(f"Query text must be string for '{query_name}'")
                db_cfg = query_cfg.get("db_config")
                db_cfg_dict = db_cfg if isinstance(db_cfg, dict) else None

                iterations: list[float] = []
                all_memory_samples: list[list[float]] = []
                for pass_num in range(1, passes + 1):
                    measurement, memory_stats, raw_samples = await run_one(query_text, db_cfg=db_cfg_dict)
                    iterations.append(measurement)
                    all_memory_samples.append(raw_samples)
                    print(
                        f"Запрос {query_name}, проход {pass_num}: "
                        f"{measurement:.3f} сек, память: медиана {memory_stats['median']:.2f} МБ "
                        f"[{memory_stats['min']:.2f}–{memory_stats['max']:.2f}]"
                    )

                memory_graph = save_memory_graph(query_name, all_memory_samples)
                total_rating_data[query_name] = iterations
                result["result"][query_name] = build_result_entry(query_name, iterations, all_memory_samples, memory_graph)
        finally:
            await pool.close()

        criteria_iterations = [float(index) for index in range(1, passes + 1)]

        # Критерии для матмодели — добавляем только те запросы, которые были запущены
        criteria_configs = [
            ("hard_query",             0.3, (0.0, 1650.0)),
            ("order_query",            0.2, (0.0, 2220.0)),
            ("substring_search_query", 0.3, (0.0,  850.0)),
            ("join_query",             0.1, (0.0,  600.0)),
            (self.oom_query_name,      0.1, (0.0, 7900.0)),
        ]
        model = MathModel()
        for crit_name, weight, bounds in criteria_configs:
            if crit_name not in total_rating_data:
                continue
            model.add_criterion(
                name=crit_name,
                iterations=criteria_iterations,
                values=[float(v) for v in total_rating_data[crit_name]],
                weight=weight,
                negative=True,
                bounds=bounds,
            )

        total_rating_info = model.total_rating(0.882)
        result["math_model"] = {
            "power": round(0.882, 6),
            "mean_abs_log_error": round(0.0446958192156943, 6),
            "selection_score": round(0.04469581921569429, 6),
        }
        result["total_rating"] = round(float(total_rating_info["total_rating"]*100))

        with open(results_file_path, "w", encoding="utf-8") as result_stream:
            json.dump(result, result_stream, ensure_ascii=False, indent=2)

        return result
