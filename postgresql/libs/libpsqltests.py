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
    #     self.db_config = {
    #     "host": "127.0.0.1",
    #     "port": 55432,
    #     "dbname": "olapdb",
    #     "user": "olap",
    #     "password": "olap",
    # }    
      
        # TODO Сделать более тяжелые запросы чтобы каждая итерация была по 10-15 мин
        self.hard_query: LiteralString = """
WITH
    binary_search AS (
        (
            SELECT
                main.build_packages.build AS build,
                main.build_packages.repository AS repository,
                main.build_packages.component AS component,
                main.build_packages.sha256 AS SHA256,
                1 AS sort_order
            FROM
                main.build_packages
                
            WHERE
                main.build_packages."binary" = 'apache2'

                AND main.build_packages."binary" LIKE '%apache2%'
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
                2 AS sort_order
            FROM
                main.build_packages
            WHERE
                (
                    main.build_packages."binary" LIKE 'apache2' || '%'
                )
                AND main.build_packages."binary" != 'apache2'
                AND main.build_packages."binary" != 'apache2'
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
            WHERE
                (
                    main.build_packages."binary" LIKE '%' || 'apache2' || '%'
                )
                AND (
                    main.build_packages."binary" NOT LIKE 'apache2' || '%'
                )
                AND main.build_packages."binary" != 'apache2'
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
        self.order_query: LiteralString = """
WITH base AS (
    SELECT
        bp.build,
        bp.repository,
        bp.component,
        bp."binary",
        bp.source,
        bp.source_version,
        bp.binary_version,
        bp.create_utc,
        coalesce(bp.binary_info_json ->> 'Description', '') AS description,
        (
            length(coalesce(bp.files, ''))
            + length(coalesce(bp.binary_info, ''))
            + length(coalesce(bp."binary", ''))
            + length(coalesce(bp.binary_info_json::text, ''))
        ) AS payload_len
    FROM main.build_packages AS bp
),
tokenized AS (
    SELECT
        b.build,
        b.repository,
        b.component,
        b."binary",
        token
    FROM base AS b
    LEFT JOIN LATERAL regexp_split_to_table(lower(b.description), '\\W+') AS token
        ON TRUE
    WHERE token IS NOT NULL AND token <> ''
),
token_stats AS (
    SELECT
        t.build,
        t.repository,
        t.component,
        t."binary",
        count(*) AS token_count,
        count(DISTINCT t.token) AS unique_token_count,
        max(length(t.token)) AS max_token_len
    FROM tokenized AS t
    GROUP BY t.build, t.repository, t.component, t."binary"
),
ranked AS (
    SELECT
        b.*,
        ts.token_count,
        ts.unique_token_count,
        ts.max_token_len,
        row_number() OVER (
            PARTITION BY b.repository, b.component
            ORDER BY b.create_utc DESC NULLS LAST, b."binary"
        ) AS row_in_repo_component,
        dense_rank() OVER (
            PARTITION BY b.source
            ORDER BY coalesce(b.source_version, '') DESC
        ) AS source_version_rank,
        percent_rank() OVER (
            PARTITION BY b.repository
            ORDER BY b.payload_len DESC
        ) AS payload_percent_rank,
        lag(b.payload_len) OVER (
            PARTITION BY b.repository, b.component
            ORDER BY b.create_utc
        ) AS prev_payload_len
    FROM base AS b
    LEFT JOIN token_stats AS ts
        ON ts.build = b.build
       AND ts.repository = b.repository
       AND ts.component = b.component
       AND ts."binary" = b."binary"
),
aggregated AS (
    SELECT
        r.repository,
        r.component,
        count(*) AS package_count,
        avg(r.payload_len)::numeric(20, 3) AS avg_payload_len,
        percentile_cont(0.50) WITHIN GROUP (ORDER BY r.payload_len) AS p50_payload_len,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY r.payload_len) AS p95_payload_len
    FROM ranked AS r
    GROUP BY r.repository, r.component
)
SELECT
    r.build,
    r.repository,
    r.component,
    r."binary",
    r.source,
    r.source_version,
    r.binary_version,
    r.create_utc,
    r.payload_len,
    r.token_count,
    r.unique_token_count,
    r.max_token_len,
    r.row_in_repo_component,
    r.source_version_rank,
    r.payload_percent_rank,
    r.prev_payload_len,
    a.package_count,
    a.avg_payload_len,
    a.p50_payload_len,
    a.p95_payload_len,
    bi.rel,
    bi.packages_sync
FROM ranked AS r
JOIN aggregated AS a
    ON a.repository = r.repository
   AND a.component = r.component
LEFT JOIN main.build_info AS bi
    ON bi.build = r.build
ORDER BY
    a.p95_payload_len DESC NULLS LAST,
    r.payload_percent_rank DESC NULLS LAST,
    coalesce(r.token_count, 0) DESC,
    r.create_utc DESC NULLS LAST,
    r."binary"
"""
        self.join_query: LiteralString = """
WITH pkg AS (
    SELECT
        bp.build,
        bp.repository,
        bp.component,
        bp."binary",
        bp.source,
        bp.source_version,
        bp.binary_version,
        bp.depends,
        bp.create_utc
    FROM main.build_packages AS bp
),
sources_expanded AS (
    SELECT
        bs.source,
        bs.source_version,
        bs.repository,
        unnest(coalesce(bs.binary_packages, ARRAY[]::text[])) AS source_binary
    FROM main.build_sources AS bs
),
dep_tokens AS (
    SELECT
        p.build,
        p.repository,
        p.component,
        p."binary",
        nullif(token, '') AS dep_token
    FROM pkg AS p
    LEFT JOIN LATERAL regexp_split_to_table(
        regexp_replace(coalesce(p.depends::text, ''), '[\\{\\}\\"\\s]+', '', 'g'),
        ','
    ) AS token ON TRUE
),
mounts AS (
    SELECT
        bi.build,
        mp.key AS mount_repo,
        mp.value AS mount_path,
        bt.build_type_item
    FROM main.build_info AS bi
    LEFT JOIN LATERAL jsonb_each_text(coalesce(bi.mount_point, '{}'::jsonb)) AS mp ON TRUE
    LEFT JOIN LATERAL unnest(coalesce(bi.build_type, ARRAY[]::text[])) AS bt(build_type_item) ON TRUE
)
SELECT
    p.build,
    p.repository,
    p.component,
    p."binary",
    p.source,
    p.source_version,
    p.binary_version,
    bi.rel,
    count(DISTINCT se.source_binary) AS source_binary_matches,
    count(DISTINCT dt.dep_token) FILTER (WHERE dt.dep_token IS NOT NULL) AS dep_token_count,
    count(DISTINCT sib."binary") AS sibling_binary_count,
    count(DISTINCT m.mount_repo) AS mount_repo_count,
    max(length(coalesce(m.mount_path, ''))) AS max_mount_path_len,
    string_agg(DISTINCT m.build_type_item, ',' ORDER BY m.build_type_item) AS build_types
FROM pkg AS p
LEFT JOIN sources_expanded AS se
    ON se.source = p.source
   AND se.repository = p.repository
   AND se.source_binary = p."binary"
LEFT JOIN dep_tokens AS dt
    ON dt.build = p.build
   AND dt.repository = p.repository
   AND dt.component = p.component
   AND dt."binary" = p."binary"
LEFT JOIN pkg AS sib
    ON sib.source = p.source
   AND sib.repository = p.repository
LEFT JOIN main.build_info AS bi
    ON bi.build = p.build
LEFT JOIN mounts AS m
    ON m.build = p.build
WHERE
    lower(coalesce(p."binary", '')) LIKE '%apache%'
    OR lower(coalesce(p.source, '')) LIKE '%apache%'
GROUP BY
    p.build,
    p.repository,
    p.component,
    p."binary",
    p.source,
    p.source_version,
    p.binary_version,
    bi.rel
ORDER BY
    sibling_binary_count DESC,
    dep_token_count DESC,
    mount_repo_count DESC,
    p.build DESC
"""
        self.interlinear_search_query: LiteralString = """
WITH docs AS (
    SELECT
        bp.build,
        bp.repository,
        bp.component,
        bp."binary",
        lower(
            concat_ws(
                ' ',
                coalesce(bp."binary", ''),
                coalesce(bp.source, ''),
                coalesce(bp.source_version, ''),
                coalesce(bp.binary_version, ''),
                coalesce(bp.binary_info, ''),
                coalesce(bp.binary_info_json ->> 'Description', ''),
                coalesce(bp.binary_info_json ->> 'Filename', '')
            )
        ) AS doc
    FROM main.build_packages AS bp
),
needles AS (
    SELECT unnest(ARRAY[
        'apache',
        'server',
        'module',
        'library',
        'http',
        'security',
        'package'
    ]::text[]) AS needle
),
matches AS (
    SELECT
        d.build,
        d.repository,
        d.component,
        d."binary",
        n.needle,
        strpos(d.doc, n.needle) AS first_pos,
        (
            length(d.doc) - length(replace(d.doc, n.needle, ''))
        ) / nullif(length(n.needle), 0) AS hit_count,
        ts_rank_cd(
            to_tsvector('simple', d.doc),
            plainto_tsquery('simple', n.needle)
        ) AS rank_score
    FROM docs AS d
    JOIN needles AS n
        ON d.doc LIKE '%' || n.needle || '%'
),
scored AS (
    SELECT
        m.build,
        m.repository,
        m.component,
        m."binary",
        count(*) AS matched_terms,
        sum(m.hit_count) AS total_hits,
        min(nullif(m.first_pos, 0)) AS first_pos,
        sum(m.rank_score) AS total_rank
    FROM matches AS m
    GROUP BY
        m.build,
        m.repository,
        m.component,
        m."binary"
    HAVING count(*) >= 2
)
SELECT
    s.build,
    s.repository,
    s.component,
    s."binary",
    s.matched_terms,
    s.total_hits,
    s.total_rank,
    s.first_pos,
    left(d.doc, 320) AS snippet
FROM scored AS s
JOIN docs AS d
    ON d.build = s.build
   AND d.repository = s.repository
   AND d.component = s.component
   AND d."binary" = s."binary"
ORDER BY
    s.matched_terms DESC,
    s.total_rank DESC,
    s.total_hits DESC,
    s.first_pos ASC NULLS LAST,
    s."binary"
"""
        self.combined_analytics_query: LiteralString = """
WITH pkg AS (
    SELECT
        bp.build,
        bp.repository,
        bp.component,
        bp."binary",
        bp.source,
        bp.source_version,
        bp.binary_version,
        bp.create_utc,
        coalesce(bp.binary_info_json ->> 'Description', '') AS description,
        (
            length(coalesce(bp.files, ''))
            + length(coalesce(bp.binary_info, ''))
            + length(coalesce(bp.depends::text, ''))
        ) AS payload_len
    FROM main.build_packages AS bp
),
search_score AS (
    SELECT
        p.build,
        p.repository,
        p.component,
        p."binary",
        count(*) FILTER (
            WHERE lower(p.description) LIKE '%' || needle || '%'
        ) AS matched_terms
    FROM pkg AS p
    CROSS JOIN LATERAL unnest(ARRAY['apache', 'http', 'module', 'server']::text[]) AS needle
    GROUP BY p.build, p.repository, p.component, p."binary"
),
dep_score AS (
    SELECT
        p.build,
        p.repository,
        p.component,
        p."binary",
        count(*) FILTER (WHERE dep_token <> '') AS dep_tokens
    FROM pkg AS p
    LEFT JOIN LATERAL regexp_split_to_table(
        regexp_replace(coalesce(p.description, ''), '[^a-zA-Z0-9]+', ' ', 'g'),
        '\\s+'
    ) AS dep_token ON TRUE
    GROUP BY p.build, p.repository, p.component, p."binary"
),
repo_stats AS (
    SELECT
        p.repository,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY p.payload_len) AS repo_p95_payload,
        avg(p.payload_len)::numeric(20, 3) AS repo_avg_payload
    FROM pkg AS p
    GROUP BY p.repository
)
SELECT
    p.build,
    p.repository,
    p.component,
    p."binary",
    p.source,
    p.source_version,
    p.binary_version,
    p.payload_len,
    coalesce(ss.matched_terms, 0) AS matched_terms,
    coalesce(ds.dep_tokens, 0) AS dep_tokens,
    rs.repo_avg_payload,
    rs.repo_p95_payload,
    (
        p.payload_len
        + coalesce(ss.matched_terms, 0) * 25
        + coalesce(ds.dep_tokens, 0) * 3
    ) AS combined_score
FROM pkg AS p
LEFT JOIN search_score AS ss
    ON ss.build = p.build
   AND ss.repository = p.repository
   AND ss.component = p.component
   AND ss."binary" = p."binary"
LEFT JOIN dep_score AS ds
    ON ds.build = p.build
   AND ds.repository = p.repository
   AND ds.component = p.component
   AND ds."binary" = p."binary"
LEFT JOIN repo_stats AS rs
    ON rs.repository = p.repository
ORDER BY
    combined_score DESC,
    rs.repo_p95_payload DESC NULLS LAST,
    p.create_utc DESC NULLS LAST
"""
        self.combined_search_join_query: LiteralString = """
WITH docs AS (
    SELECT
        bp.build,
        bp.repository,
        bp.component,
        bp."binary",
        bp.source,
        bp.source_version,
        lower(
            concat_ws(
                ' ',
                coalesce(bp."binary", ''),
                coalesce(bp.source, ''),
                coalesce(bp.binary_info, ''),
                coalesce(bp.binary_info_json::text, '')
            )
        ) AS doc
    FROM main.build_packages AS bp
),
sources_expanded AS (
    SELECT
        bs.source,
        bs.source_version,
        bs.repository,
        unnest(coalesce(bs.binary_packages, ARRAY[]::text[])) AS source_binary
    FROM main.build_sources AS bs
),
mounts AS (
    SELECT
        bi.build,
        mp.key AS mount_repo,
        mp.value AS mount_path
    FROM main.build_info AS bi
    LEFT JOIN LATERAL jsonb_each_text(coalesce(bi.mount_point, '{}'::jsonb)) AS mp ON TRUE
),
scored AS (
    SELECT
        d.build,
        d.repository,
        d.component,
        d."binary",
        d.source,
        d.source_version,
        count(*) FILTER (
            WHERE d.doc LIKE '%' || needle || '%'
        ) AS term_hits
    FROM docs AS d
    CROSS JOIN LATERAL unnest(ARRAY['apache', 'http', 'module', 'repo', 'package']::text[]) AS needle
    GROUP BY d.build, d.repository, d.component, d."binary", d.source, d.source_version
)
SELECT
    s.build,
    s.repository,
    s.component,
    s."binary",
    s.source,
    s.source_version,
    s.term_hits,
    count(DISTINCT se.source_binary) AS source_binary_matches,
    count(DISTINCT m.mount_repo) AS mount_repo_count,
    max(length(coalesce(m.mount_path, ''))) AS max_mount_path_len
FROM scored AS s
LEFT JOIN sources_expanded AS se
    ON se.source = s.source
   AND se.repository = s.repository
   AND se.source_version = s.source_version
   AND se.source_binary = s."binary"
LEFT JOIN mounts AS m
    ON m.build = s.build
GROUP BY
    s.build,
    s.repository,
    s.component,
    s."binary",
    s.source,
    s.source_version,
    s.term_hits
HAVING s.term_hits >= 2
ORDER BY
    s.term_hits DESC,
    source_binary_matches DESC,
    mount_repo_count DESC,
    s.build DESC
"""

        self.passes = 3
        self.available_queries: dict[str, dict[str, object]] = {
            "hard_query": {
                "query": self.hard_query,
                "normalization_bounds": (0.0, 1.0),
            },
            "order_query": {
                "query": self.order_query,
                "normalization_bounds": (0.0, 1.0),
            },
            "join_query": {
                "query": self.join_query,
                "normalization_bounds": (0.0, 1.0),
            },
            "interlinear_search_query": {
                "query": self.interlinear_search_query,
                "normalization_bounds": (0.0, 1.0),
            },
            "combined_analytics_query": {
                "query": self.combined_analytics_query,
                "normalization_bounds": (0.0, 1.0),
            },
            "combined_search_join_query": {
                "query": self.combined_search_join_query,
                "normalization_bounds": (0.0, 1.0),
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
            return sorted_values[lower_index] + (
                sorted_values[upper_index] - sorted_values[lower_index]
            ) * fraction

        def calc_median(values):
            if not values:
                return 0.0
            sorted_values = sorted(values)
            mid = len(sorted_values) // 2
            if len(sorted_values) % 2 == 0:
                return (sorted_values[mid - 1] + sorted_values[mid]) / 2
            return sorted_values[mid]

        def calc_total_rating_overall(total_rating_data):
            # Упрощенная модель:
            # 1) Все критерии негативные: чем меньше время, тем лучше.
            # 2) Вес каждого запроса одинаковый: 1 / количество запросов.
            # 3) Нормализация по статическим границам из self.available_queries[*]["normalization_bounds"].
            # 4) Интегральная оценка по запросу = нормированная площадь под кривой.
            # 5) Итоговый рейтинг = сумма(weight_i * score_i) * 100.
            # total_rating_data формат: {"query_name": [iter1, iter2, ...], ...}

            def normalize_query_iterations(query_name, iterations):
                if not iterations:
                    return []

                query_cfg = self.available_queries.get(query_name, {})
                bounds = query_cfg.get("normalization_bounds")
                if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
                    raise ValueError(
                        f"Set normalization_bounds=(lower, upper) for '{query_name}' in available_queries"
                    )
                lower_bound, upper_bound = float(bounds[0]), float(bounds[1])
                span = upper_bound - lower_bound
                if span <= 0:
                    raise ValueError(
                        f"Invalid normalization_bounds for '{query_name}': lower must be < upper"
                    )

                # Негативный критерий: меньше время = лучше.
                normalized = [
                    (upper_bound - value) / span
                    for value in iterations
                ]
                return [min(1.0, max(0.0, value)) for value in normalized]

            def calc_integral_score(normalized_values):
                if not normalized_values:
                    return 0.0
                if len(normalized_values) == 1:
                    return normalized_values[0]

                # Аппроксимация линейной функцией между соседними точками.
                x_values = list(range(1, len(normalized_values) + 1))
                area = 0.0
                for index in range(len(normalized_values) - 1):
                    left_x = x_values[index]
                    right_x = x_values[index + 1]
                    left_y = normalized_values[index]
                    right_y = normalized_values[index + 1]
                    area += (left_y + right_y) * (right_x - left_x) / 2.0

                x_span = x_values[-1] - x_values[0]
                if x_span <= 0:
                    return normalized_values[0]

                score = area / x_span
                return min(1.0, max(0.0, score))

            if not total_rating_data:
                return 0.0

            query_scores = {}
            for query_name, iterations in total_rating_data.items():
                normalized_values = normalize_query_iterations(query_name, iterations)
                integral_score = calc_integral_score(normalized_values)
                query_scores[query_name] = integral_score

            query_weight = 1.0 / len(query_scores)
            weighted_score = 0.0
            for query_score in query_scores.values():
                weighted_score += query_score * query_weight

            weighted_score = min(1.0, max(0.0, weighted_score))
            return weighted_score * 100.0

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
            async def run_one(query_text: LiteralString) -> float:
                started = time.perf_counter()
                async with pool.connection(timeout=self.pool_connection_timeout) as conn:
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

            total_rating_overall = calc_total_rating_overall(total_rating_data)
            result["total_rating"] = round(total_rating_overall, 3)
        finally:
            await pool.close()

        with open(results_file_path, "w", encoding="utf-8") as result_stream:
            json.dump(result, result_stream, ensure_ascii=False, indent=2)

        return result
    
