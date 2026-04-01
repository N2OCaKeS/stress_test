import re
import os
import json
import logging
import time
from typing import LiteralString
import psycopg
from psycopg_pool import AsyncConnectionPool
import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt


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
        }

        self.pool_connection_timeout = 600.0
        self.results_file = f"{REPORT_PATH}/olap_results.json"

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
        prepared_queries = []
        for query_name, query_cfg in self.available_queries.items():
            query_text = query_cfg.get("query")
            if not isinstance(query_text, str):
                raise ValueError(f"Query text must be string for '{query_name}'")
            prepared_queries.append((query_name, query_text))

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

        pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=1,
            open=False,
        )

        os.makedirs(report_dir, exist_ok=True)

        await pool.open()
        try:

            async def run_one(query_text: str) -> float:
                started = time.perf_counter()
                async with pool.connection(
                    timeout=self.pool_connection_timeout
                ) as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(query_text)
                        await cur.fetchall()
                return time.perf_counter() - started

            result = {
                "time_unit": "seconds",
                "result": {},
            }
            total_rating_data = {}

            for query_name, query_text in prepared_queries:
                iterations = []
                for pass_num in range(1, passes + 1):
                    measurement = await run_one(query_text)
                    iterations.append(measurement)
                    print(
                        f"Запрос {query_name}, проход {pass_num}: "
                        f"{measurement:.3f} сек"
                    )

                median_value = calc_median(iterations)
                p99_value = calc_percentile(iterations, 99)
                p95_value = calc_percentile(iterations, 95)
                p50_value = calc_percentile(iterations, 50)
                min_value = min(iterations) if iterations else 0.0
                max_value = max(iterations) if iterations else 0.0
                total_rating_data[query_name] = iterations
                graph_file_path = save_speed_graph(query_name, iterations)

                result["result"][query_name] = {
                    "iterations": [round(value, 3) for value in iterations],
                    "median": round(median_value, 3),
                    "p99": round(p99_value, 3),
                    "p95": round(p95_value, 3),
                    "p50": round(p50_value, 3),
                    "min": round(min_value, 3),
                    "max": round(max_value, 3),
                    "speed_graph": graph_file_path,
                }
            criteria_iterations = [float(index) for index in range(1, passes + 1)]

            model = MathModel()
            model.add_criterion(
                name="hard_query",
                iterations=criteria_iterations,
                values=[float(value) for value in result["result"]["hard_query"]["iterations"]],
                weight=0.3,
                negative=True,
                bounds=(0.0, 270.0),
            )
            model.add_criterion(
                name="order_query",
                iterations=criteria_iterations,
                values=[float(value) for value in result["result"]["order_query"]["iterations"]],
                weight=0.2,
                negative=True,
                bounds=(0.0, 410.0),
            )
            model.add_criterion(
                name="substring_search_query",
                iterations=criteria_iterations,
                values=[float(value) for value in result["result"]["substring_search_query"]["iterations"]],
                weight=0.3,
                negative=True,
                bounds=(0.0, 120.0),
            )
            model.add_criterion(
                name="join_query",
                iterations=criteria_iterations,
                values=[float(value) for value in result["result"]["join_query"]["iterations"]],
                weight=0.2,
                negative=True,
                bounds=(0.0, 50.0),
            )
            power_info = model.calc_power()
            total_rating_info = model.total_rating(power_info["power"])
            result["math_model"] = {
                "power": round(float(power_info["power"]), 6),
                "mean_abs_log_error": round(float(power_info["mean_abs_log_error"]), 6),
                "selection_score": round(float(power_info["selection_score"]), 6),
            }
            result["total_rating"] = round(float(total_rating_info["total_rating"]), 3)
        finally:
            await pool.close()

        with open(results_file_path, "w", encoding="utf-8") as result_stream:
            json.dump(result, result_stream, ensure_ascii=False, indent=2)

        return result
