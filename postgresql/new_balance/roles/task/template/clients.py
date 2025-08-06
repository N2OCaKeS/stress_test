import threading
from random import choice, randint
import psycopg2
import traceback
from datetime import datetime

# Конфигурация
N_ACCOUNTS = 100000
NUM_CLIENTS = 20
NUM_TRANSACTIONS = 1000
SCALE_FACTOR = 100

# Параметры подключения к БД
# # # Change parameters accordingly
# Database connection parameters
DB_PARAMS = {
    "dbname": "contrprimer",
    "user": "postgres",
    "host": "pgpool.balance.rbt",
    "port": "5440",
    "connect_timeout": 0,
    "application_name": "pgbench_test"    
}

# SQL запросы
QUERIES = [
    "SELECT abalance FROM pgbench_accounts WHERE aid = %s;",
    "UPDATE pgbench_accounts SET abalance = abalance + %s WHERE aid = %s;",
    "DELETE FROM pgbench_accounts WHERE aid = %s;"
]

# Потокобезопасный счетчик результатов
class ResultCounter:
    def __init__(self):
        self.success = 0
        self.fail = 0
        self.lock = threading.Lock()
    
    def add_success(self):
        with self.lock:
            self.success += 1
    
    def add_fail(self):
        with self.lock:
            self.fail += 1
    
    def get_stats(self):
        with self.lock:
            return self.success, self.fail

results = ResultCounter()

def run_pgbench():
    for _ in range(NUM_TRANSACTIONS):
        try:
            with psycopg2.connect(**DB_PARAMS) as conn:
                with conn.cursor() as cur:
                    query = choice(QUERIES)
                    if query.startswith('UPDATE'):
                        cur.execute(query, (randint(-5000, 5000), randint(1, N_ACCOUNTS * SCALE_FACTOR)))
                    else:
                        cur.execute(query, [randint(1, N_ACCOUNTS * SCALE_FACTOR)])
                    
                    conn.commit()
                    results.add_success()
                    
                    # Выводим статистику каждые 100 успешных запросов
                    if results.success % 100 == 0:
                        s, f = results.get_stats()
                        print(f"Success: {s}, Fail: {f}")
                        
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
            results.add_fail()
            log_error(e)
            continue
        except Exception as e:
            results.add_fail()
            log_error(e)
            raise

def log_error(e):
    with open('errors.log', 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"{str(e)}\n")
        f.write(f"{traceback.format_exc()}\n\n")

def main():
    threads = []
    for _ in range(NUM_CLIENTS):
        t = threading.Thread(target=run_pgbench)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Вывод итоговой статистики
    total_queries = NUM_TRANSACTIONS * NUM_CLIENTS
    success, fail = results.get_stats()
    
    stats = (
        f"Number of successful queries: {success}\n"
        f"Number of failed queries: {fail}\n"
        f"Percent of failed queries: {(fail / total_queries) * 100:.2f}% "
        f"({fail} / {total_queries})"
    )
    
    print(stats)
    with open('results_balance.txt', 'w') as f:
        f.write(stats)

if __name__ == '__main__':
    main()