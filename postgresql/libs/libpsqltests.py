import re
import os
import json
import logging
import time
import asyncio
from typing import LiteralString
import psycopg
from psycopg_pool import AsyncConnectionPool


from psb_conf import LOG_FILENAME, DATABASE_NAME, \
    MAC_SQL_UPGRADE, MAC_SQL_TRANSACTION, \
    TABLESPACE_DEFAULT, REPORT_FILENAME, REPORT_PATH, PG_SETEST_PORT, PG_VERSION, PG_VERSION_18
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
        "port": 5432,
        "dbname": "your_db",
        "user": "your_user",
        "password": "your_password",
    }

        self.query: LiteralString = """

WITH
    binary_search AS (
        (
            SELECT
                main.build_packages_count.build AS build,
                main.build_packages_count.repository AS repository,
                main.build_packages_count.component AS component,
                main.build_packages_count.sha256 AS SHA256,
                1 AS sort_order
            FROM
                main.build_packages_count
                JOIN main.view_subversion_type ON main.view_subversion_type.subversion = main.build_packages_count.build
            WHERE
                main.build_packages_count."binary" = 'apache2'
                AND main.view_subversion_type.other = TRUE
                AND main.build_packages_count."binary" LIKE '%apache2%'
            ORDER BY
                main.build_packages_count.create_utc DESC,
                main.build_packages_count."binary"
        )
        UNION ALL
        (
            SELECT
                main.build_packages.build AS build,
                main.build_packages.repository AS repository,
                main.build_packages.component AS component,
                main.build_packages.sha256 AS SHA256,
                2 AS sort_order
            FROM
                main.build_packages
                JOIN main.view_subversion_type ON main.view_subversion_type.subversion = main.build_packages.build
            WHERE
                (
                    main.build_packages."binary" LIKE 'apache2' || '%'
                )
                AND main.build_packages."binary" != 'apache2'
                AND main.build_packages."binary" != 'apache2'
                AND main.view_subversion_type.other = TRUE
            ORDER BY
                main.build_packages.create_utc DESC,
                main.build_packages."binary"
        )
        UNION ALL
        (
            SELECT
                main.build_packages.build AS build,
                main.build_packages.repository AS repository,
                main.build_packages.component AS component,
                main.build_packages.sha256 AS SHA256,
                3 AS sort_order
            FROM
                main.build_packages
                JOIN main.view_subversion_type ON main.view_subversion_type.subversion = main.build_packages.build
            WHERE
                (
                    main.build_packages."binary" LIKE '%' || 'apache2' || '%'
                )
                AND (
                    main.build_packages."binary" NOT LIKE 'apache2' || '%'
                )
                AND main.build_packages."binary" != 'apache2'
                AND main.view_subversion_type.other = TRUE
            ORDER BY
                main.build_packages.create_utc DESC,
                main.build_packages."binary"
        )
    ),
    "buildPackageSearch" AS (
        SELECT
            binary_search.build AS build,
            binary_search.repository AS repository,
            binary_search.component AS component,
            binary_search.sha256 AS SHA256
        FROM
            binary_search
        LIMIT
            50
        OFFSET
            0
    )
SELECT
    build_packages_1.build,
    build_packages_1.repository,
    build_packages_1.component,
    build_packages_1."binary",
    build_packages_1.binary_version,
    build_packages_1.source,
    build_packages_1.source_version,
    concat_ws(
        '/',
        jsonb_extract_path_text(
            build_info_1.mount_point,
            build_packages_1.repository
        ),
        jsonb_extract_path_text(build_packages_1.binary_info_json, 'Filename')
    ) AS deb_href,
    concat_ws(
        '_',
        build_packages_1.build,
        build_packages_1.repository,
        build_packages_1.component,
        build_packages_1."binary"
    ) AS pack_id,
    split_part(
        coalesce(
            jsonb_extract_path_text(build_packages_1.binary_info_json, 'Description'),
            ''
        ),
        '
',
        1
    ) AS description,
    build_info_1.rel,
    build_packages_1.sha256,
    build_info_1.build_type,
    build_info_1.packages_sync
FROM
    "buildPackageSearch"
    JOIN main.build_packages AS build_packages_1 ON build_packages_1.build = "buildPackageSearch".build
    AND build_packages_1.repository = "buildPackageSearch".repository
    AND build_packages_1.component = "buildPackageSearch".component
    AND build_packages_1.sha256 = "buildPackageSearch".sha256
    JOIN main.build_info AS build_info_1 ON build_info_1.build = "buildPackageSearch".build
ORDER BY
    CASE
        WHEN (build_packages_1."binary" = 'apache2') THEN 1
        WHEN (build_packages_1."binary" LIKE 'apache2%') THEN 2
        ELSE 3
    END,
    build_packages_1.create_utc DESC,
    CASE
        WHEN (build_packages_1.repository = 'main') THEN 1
        WHEN (
            split_part(build_packages_1.repository, '-', 1) = 'installation'
        ) THEN 2
        WHEN (build_packages_1.repository = 'repository') THEN 3
        WHEN (build_packages_1.repository = 'update') THEN 4
        WHEN (build_packages_1.repository = 'devel') THEN 5
        WHEN (build_packages_1.repository = 'dev-update') THEN 6
        WHEN (build_packages_1.repository = 'base') THEN 7
        WHEN (
            split_part(build_packages_1.repository, '-', 1) = 'extended'
        ) THEN 8
        WHEN (
            split_part(build_packages_1.repository, '-', 1) = 'md'
        ) THEN 9
        ELSE 10
    END,
    build_packages_1."binary"
"""  
        self.passes = 3
        self.parallel_queries = 10
        self.single_results_file = f"{REPORT_PATH}/olap_single_results.json"
        self.multi_results_file = f"{REPORT_PATH}/olap_multi_results.json"

    async def run_test(self):
        db_config = self.db_config


        dsn = (
            f"host={db_config['host']} "
            f"port={db_config['port']} "
            f"dbname={db_config['dbname']} "
            f"user={db_config['user']} "
            f"password={db_config['password']}"
        )

        single_result = []
        milti_result = {}

        pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=self.parallel_queries,
            max_size=self.parallel_queries,
            open=False,
        )

        await pool.open()
        try:
            async def run_one() -> float:
                started = time.perf_counter()
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(self.query)
                        await cur.fetchall()
                elapsed_seconds = time.perf_counter() - started
                return round(elapsed_seconds, 3)

            for query_num in range(1, 11):
                one_result = await run_one()
                single_result.append(one_result)
                print(f"Одиночный запрос {query_num}: {one_result:.3f} сек")

            for pass_num in range(1, self.passes + 1):
                pass_result = await asyncio.gather(
                    *(run_one() for _ in range(self.parallel_queries))
                )
                milti_result[pass_num] = list(pass_result)
                formatted_pass_result = [f"{value:.3f}" for value in milti_result[pass_num]]
                print(f"Параллельный проход {pass_num}: {formatted_pass_result} сек")
        finally:
            await pool.close()

        single_result_seconds = [f"{value:.3f}" for value in single_result]
        multi_result_seconds = {
            str(key): [f"{value:.3f}" for value in values]
            for key, values in milti_result.items()
        }

        single_payload = {
            "time_unit": "seconds",
            "single_result": single_result_seconds,
        }
        multi_payload = {
            "time_unit": "seconds",
            "multi_result": multi_result_seconds,
        }

        os.makedirs(REPORT_PATH, exist_ok=True)
        with open(self.single_results_file, "w", encoding="utf-8") as single_stream:
            json.dump(single_payload, single_stream, ensure_ascii=False, indent=2)
        with open(self.multi_results_file, "w", encoding="utf-8") as multi_stream:
            json.dump(multi_payload, multi_stream, ensure_ascii=False, indent=2)

        return single_result, milti_result
    
