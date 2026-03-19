import re
import os
import json
import logging
import time
import asyncio
from typing import LiteralString, cast
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
        "port": 55432,
        "dbname": "olapdb",
        "user": "olap",
        "password": "olap",
    }
      

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
LIMIT 800;
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
LIMIT 900;
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
LIMIT 900;
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
LIMIT 1000;
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
LIMIT 1000;
"""

        self.queries: dict[str, dict[str, object]] = {
            "hard_query": {
                "query": self.hard_query,
                "concurrent_levels": [1, 5, 10],
                "passes": 1,
            },
            "order_query": {
                "query": self.order_query,
                "concurrent_levels": [1, 5, 10],
                "passes": 1,
            },
            "join_query": {
                "query": self.join_query,
                "concurrent_levels": [1, 5, 10],
                "passes": 1,
            },
            "interlinear_search_query": {
                "query": self.interlinear_search_query,
                "concurrent_levels": [1, 5, 10],
                "passes": 1,
            },
            "combined_analytics_query": {
                "query": self.combined_analytics_query,
                "concurrent_levels": [1, 5, 10],
                "passes": 1,
            },
            "combined_search_join_query": {
                "query": self.combined_search_join_query,
                "concurrent_levels": [1, 5, 10],
                "passes": 1,
            },
        }

        self.pool_connection_timeout = 600.0
        self.results_file = f"{REPORT_PATH}/olap_results.json"

    async def run_test(self):
        db_config = self.db_config


        dsn = (
            f"host={db_config['host']} "
            f"port={db_config['port']} "
            f"dbname={db_config['dbname']} "
            f"user={db_config['user']} "
            f"password={db_config['password']}"
        )

        def normalize_query_params(query_cfg):
            query_text = query_cfg.get("query")
            if not isinstance(query_text, str):
                raise ValueError("query must be a SQL string")

            raw_levels = query_cfg.get("concurrent_levels", [1])
            levels = []
            if isinstance(raw_levels, (list, tuple)):
                for level in raw_levels:
                    int_level = int(level)
                    if int_level > 0:
                        levels.append(int_level)
            if not levels:
                levels = [1]

            passes = int(query_cfg.get("passes", 1))
            if passes < 1:
                passes = 1

            return cast(LiteralString, query_text), levels, passes

        prepared_queries = []
        for query_name, query_cfg in self.queries.items():
            query_text, concurrent_levels, passes = normalize_query_params(query_cfg)
            prepared_queries.append((query_name, query_text, concurrent_levels, passes))

        max_concurrency = max(
            max(concurrent_levels) for _, _, concurrent_levels, _ in prepared_queries
        )

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

        def calc_median_by_index(iterations):
            if not iterations:
                return []
            values_count = len(iterations[0])
            medians = []
            for index in range(values_count):
                values_at_index = [iteration[index] for iteration in iterations]
                sorted_values = sorted(values_at_index)
                mid = len(sorted_values) // 2
                if len(sorted_values) % 2 == 0:
                    medians.append((sorted_values[mid - 1] + sorted_values[mid]) / 2)
                else:
                    medians.append(sorted_values[mid])
            return medians

        pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=max_concurrency,
            open=False,
        )

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

            for query_name, query_text, concurrent_levels, passes in prepared_queries:
                result["result"][query_name] = {}

                for concurrency in concurrent_levels:
                    level_key = str(concurrency)
                    level_iterations = []
                    level_result = {}

                    for pass_num in range(1, passes + 1):
                        pass_result = await asyncio.gather(
                            *(run_one(query_text) for _ in range(concurrency))
                        )
                        pass_result_list = list(pass_result)
                        level_iterations.append(pass_result_list)

                        rounded_pass_result = [round(value, 3) for value in pass_result_list]
                        level_result[str(pass_num)] = rounded_pass_result
                        formatted_pass_result = [f"{value:.3f}" for value in rounded_pass_result]
                        print(
                            f"Запрос {query_name}, уровень {concurrency}, "
                            f"проход {pass_num}: {formatted_pass_result} сек"
                        )

                    median_values = calc_median_by_index(level_iterations)
                    rounded_median_values = [round(value, 3) for value in median_values]
                    result["result"][query_name][level_key] = {
                        "result": level_result,
                        "median": rounded_median_values,
                        "p99": round(calc_percentile(median_values, 99), 3),
                        "p95": round(calc_percentile(median_values, 95), 3),
                        "p50": round(calc_percentile(median_values, 50), 3),
                        "min": round(min(median_values), 3) if median_values else 0.0,
                        "max": round(max(median_values), 3) if median_values else 0.0,
                    }
        finally:
            await pool.close()

        os.makedirs(REPORT_PATH, exist_ok=True)
        with open(self.results_file, "w", encoding="utf-8") as result_stream:
            json.dump(result, result_stream, ensure_ascii=False, indent=2)

        return result
    
