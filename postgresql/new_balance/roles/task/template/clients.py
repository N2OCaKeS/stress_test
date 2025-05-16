import threading
from random import choice, randint
from os.path import exists, isfile
from os import chmod, chown
import subprocess
import psycopg2

N_ACCOUNTS = 100000

# Параметры подключения к базе данных
db_params = {
    "dbname": 'contrprimer',
    "user": "postgres",
    "host": "pgpool.balance.rbt",
    "port": "5440"
}

# Количество клиентов/потоков для симуляции
num_clients = 20

# Количество транзакций на каждый поток
num_transactions = 10000  # ~3-6 минут 1000 ~30 sec

scale_factor = 100

# SQL-запросы для исполнения
queries = [
    "SELECT abalance FROM pgbench_accounts WHERE aid = %s;",
    "UPDATE pgbench_accounts SET abalance = abalance + %s WHERE aid = %s;",
    "DELETE FROM pgbench_accounts WHERE aid = %s;"
]

# Глобальный список для хранения результатов каждого потока
thread_results = []


def run_pgbench():
    # Локальные счётчики для каждого потока
    local_success = 0
    local_fail = 0
    
    for _ in range(num_transactions):
        try:
            with psycopg2.connect(**db_params) as conn:
                cur = conn.cursor()
                query = choice(queries)
                
                if query.startswith('UPDATE'):
                    cur.execute(query, (randint(-5000, 5000), randint(1, N_ACCOUNTS * scale_factor)))
                else:
                    cur.execute(query, [randint(1, N_ACCOUNTS * scale_factor)])
                
                conn.commit()
                local_success += 1
        except Exception as e:
            with open("error.log", "a", encoding="utf-8") as file:
                file.write(f"Возникла ошибка: {e}\n")
            local_fail += 1
            
    # Добавляем результаты текущего потока в глобальный список
    thread_results.append({'success': local_success, 'fail': local_fail})


def astra_version():
    version = ['null', 'null']
   
    if exists("/etc/astra_version"):
        with open("/etc/astra_version", "r") as file:
            astra_update_version = file.read()
        version[0] = astra_update_version.strip('\n')

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
    if av[0].startswith('1.7'):
         psql_version = 'postgresql-11'
    elif av[0].startswith('1.8'):
         psql_version = 'postgresql-15'

    info_lst = [f'{av[0]}({av[1]})\n',
                cmd('uname -r'),
                cmd(f'apt-cache show {psql_version}', regex=True) + '\n',
                cmd('apt-cache show pgpool2', regex=True) + '\n',
                psql_version + '\n',
                'pgpool2']
    
    with open('psb_info.txt', 'a+') as info:
        info.writelines(info_lst)

    if isfile('available_packages.txt'):
        cmd('sudo chown $USER:$USER available_packages.txt')
    with open('available_packages.txt', 'a') as file:
         pkgs = cmd('apt list postgresql*')
         file.write('\n2nd iteration:\n')
         file.write(pkgs)


def main():
    threads = []
    
    # Запуск потоков
    for i in range(num_clients):
        t = threading.Thread(target=run_pgbench)
        threads.append(t)
        t.start()

    # Ожидание завершения всех потоков
    for t in threads:
        t.join()

    # Суммируем результаты всех потоков
    total_success = sum(result['success'] for result in thread_results)
    total_fail = sum(result['fail'] for result in thread_results)
    all_queries = num_transactions * num_clients

    success_quer = f"Number of successful queries: {total_success}"
    failed_quer = f"Number of failed queries: {total_fail}"
    failed_quer_perc = f"Percent of failed queries: {(total_fail / all_queries) * 100:.2f}% " \
                        f"({total_fail} / {all_queries})"
    
    with open('results_balance.txt', 'w') as results_file:
        results_file.write(success_quer + '\n')
        results_file.write(failed_quer + '\n')
        results_file.write(failed_quer_perc + '\n')

    print(success_quer)
    print(failed_quer)
    print(failed_quer_perc)
    info_list()


if __name__ == '__main__':
    main()
