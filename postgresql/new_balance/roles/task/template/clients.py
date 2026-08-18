import threading
import time
import subprocess
import traceback
import os
import select
from itertools import count
from datetime import datetime
from os.path import exists, isfile

import psycopg2
from psycopg2 import Error, OperationalError, extensions

# ===================== Конфиг =====================
NUM_CLIENTS = 20
NUM_TRANSACTIONS = 10000  # на поток
DB_WAIT_TIMEOUT = 0.2
OUTPUT_DIR = "/tmp/clients-test"

DB_PARAMS = {
    "dbname": "test",
    "user": "postgres",
    "host": "pgpool.balance.rbt",
    "port": "5440",
    "connect_timeout": 1,
    "application_name": "pgbench_test",
    "options": "-c statement_timeout=1000 -c lock_timeout=1000",
}


# DB_HOST = "localhost"
# DB_PORT = "5434"
# DB_NAME = "allta_vm"
# DB_USER = "allta"
# DB_PASSWORD = "team13"  # <-- задайте при необходимости

# DB_PARAMS = {
#     "host": DB_HOST,
#     "port": DB_PORT,
#     "dbname": DB_NAME,
#     "user": DB_USER,
#     "password": DB_PASSWORD,
#     "connect_timeout": 2,
#     "application_name": "cmd_vs_psy",
#     "application_name": "pgbench_test",    
# }

# ===================== Счётчики =====================
class ResultCounter:
    def __init__(self):
        self.success = 0
        self.fail = 0
        self.lock = threading.Lock()
    def add_success(self):
        with self.lock: self.success += 1
    def add_fail(self):
        with self.lock: self.fail += 1
    def get_stats(self):
        with self.lock: return self.success, self.fail

results = ResultCounter()


def wait_callback(conn):
    deadline = time.monotonic() + DB_WAIT_TIMEOUT
    while True:
        state = conn.poll()
        if state == extensions.POLL_OK:
            return
        if state == extensions.POLL_READ:
            rlist, wlist = [conn.fileno()], []
        elif state == extensions.POLL_WRITE:
            rlist, wlist = [], [conn.fileno()]
        else:
            raise OperationalError(f"unexpected libpq poll state: {state}")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OperationalError(f"database operation timed out after {DB_WAIT_TIMEOUT:.3f}s")
        ready_r, ready_w, _ = select.select(rlist, wlist, [], remaining)
        if not ready_r and not ready_w:
            raise OperationalError(f"database operation timed out after {DB_WAIT_TIMEOUT:.3f}s")


extensions.set_wait_callback(wait_callback)

# Глобальная нумерация запросов (потокобезопасно)
_tx_lock = threading.Lock()
_tx_counter = count(1)
def next_no():
    with _tx_lock:
        return next(_tx_counter)

# ===================== Логи =====================
def output_path(filename):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, filename)


def log_error(e, no=None):
    with open(output_path('errors.log'), 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n")
        if no is not None:
            f.write(f"no={no}\n")
        f.write(f"{repr(e)}\n")
        f.write(f"{traceback.format_exc()}\n\n")

# ===================== DDL =====================
def ensure_table():
    ddl = """
    CREATE TABLE IF NOT EXISTS test (
      value BIGINT PRIMARY KEY,
      time  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """
    with psycopg2.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

# ===================== Прогрев =====================
def prewarm_connection():
    with psycopg2.connect(**DB_PARAMS) as c:
        with c.cursor() as cur:
            cur.execute("SELECT 1;")

# ===================== Основной цикл =====================
def run_worker():
    for _ in range(NUM_TRANSACTIONS):
        no = next_no()
        try:
            with psycopg2.connect(**DB_PARAMS) as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO test (value) VALUES (%s);", (no,))
            results.add_success()
            s, f = results.get_stats()
            if s % 100 == 0:
                print(f"Success: {s}, Fail: {f}")
        except Error as e:
            results.add_fail()
            log_error(e, no=no)
            continue
        except Exception as e:
            results.add_fail()
            s, f = results.get_stats()
            print(f"Success: {s}, Fail: {f}")
            log_error(e, no=no)
            continue

# ======= Диагностика окружения (оставил как было, опционально) =========
def astra_version():
    version = ['null', 'null']
    if exists("/etc/astra_version"):
        with open("/etc/astra_version", "r") as file:
            version[0] = file.read().strip('\n')
    if exists("/etc/astra_license"):
        with open("/etc/astra_license", "r") as file:
            astra_license = file.read()
            if "orel" in astra_license:
                version[1] = "orel"
            elif "smolensk" in astra_license:
                version[1] = "smolensk"
            elif "voronezh" in astra_license:
                version[1] = "voronezh"
            else:
                print("Version of distribution not found")
    return version

def info_list():
    import re
    def cmd(command, regex=False):
        output = subprocess.run(command, shell=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
        if regex:
            match = re.search(r'Version: (\S+)', output)
            return match.group(1) if match else "N/A"
        return output
    av = astra_version()
    psql_version = 'postgresql-15' if av[0].startswith('1.8') else 'postgresql-11'
    info_lst = [f'{av[0]}({av[1]})\n',
                cmd('uname -r'),
                cmd(f'apt-cache show {psql_version}', regex=True) + '\n',
                cmd('apt-cache show pgpool2', regex=True) + '\n',
                psql_version + '\n',
                'pgpool2']
    with open(output_path('psb_info.txt'), 'a+') as info:
        info.writelines(info_lst)
    if isfile(output_path('available_packages.txt')):
        subprocess.run(f"sudo chown $USER:$USER {output_path('available_packages.txt')}", shell=True)
    with open(output_path('available_packages.txt'), 'a') as file:
        pkgs = cmd('apt list postgresql*')
        file.write('\n2nd iteration:\n')
        file.write(pkgs)

# ===================== Точка входа =====================
def main():
    # Таблица создаётся заранее в db.py при настройке БД / Table is pre-created in db.py during DB setup
    open(output_path('errors.log'), 'a', encoding='utf-8').close()
    threads = []
    for _ in range(NUM_CLIENTS):
        t = threading.Thread(target=run_worker, daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    total = NUM_TRANSACTIONS * NUM_CLIENTS
    success, fail = results.get_stats()
    info_list()
    stats = (
        f"Number of successful queries: {success}\n"
        f"Number of failed queries: {fail}\n"
        f"Percent of failed queries: {(fail / total) * 100:.2f}% "
        f"({fail} / {total})"
    )
    print(stats)
    with open(output_path('results_balance.txt'), 'w') as f:
        f.write(stats)

if __name__ == '__main__':
    main()
