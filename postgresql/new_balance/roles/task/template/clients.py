import threading
from random import choice, randint
from os.path import exists, isfile
from os import chmod, chown
import subprocess
import psycopg2

N_ACCOUNTS = 100000

# # # Change parameters accordingly
# Database connection parameters
db_params = {
    "dbname": 'contrprimer',
    "user": "postgres",
    "host": "10.177.103.131",
    "port": "5440"
}

# Number of clients/threads to simulate
num_clients = 20

# Number of transactions per client
num_transactions = 10000  # ~3-6 minutes

scale_factor = 100
# # #


# SQL queries to execute
queries = [
    "SELECT abalance FROM pgbench_accounts WHERE aid = %s;",
    "UPDATE pgbench_accounts SET abalance = abalance + %s WHERE aid = %s;",
    "DELETE FROM pgbench_accounts WHERE aid = %s;"
]

# Dictionary to store the results
results = {'success': 0, 'fail': 0}


def run_pgbench():
    global results
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
                results['success'] += 1
        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            results['fail'] += 1


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
        #chown('available_packages.txt', 1001, 1002)
        #chmod('available_packages.txt', 0o777)
    with open('available_packages.txt', 'a') as file:
         pkgs = cmd('apt list postgresql*')
         file.write('\n2nd iteration:\n')
         file.write(pkgs)


def main():
    threads = []
    for i in range(num_clients):
        t = threading.Thread(target=run_pgbench)
        threads.append(t)
        t.start()

    # Wait for all threads to finish
    for t in threads:
        t.join()

    # Process and print results
    all_queries = num_transactions * num_clients
    success_quer = f"Number of successful queries: {results['success']}"
    failed_quer = f"Number of failed queries: {results['fail']}"
    failed_quer_perc = f"Percent of failed queries: {(results['fail'] / all_queries) * 100:.2f}% " \
                       f"({results['fail']} / {all_queries})"
    
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
