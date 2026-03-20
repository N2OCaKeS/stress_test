import argparse
import os
import subprocess
import json
import asyncio

from time import time, strftime, gmtime, sleep, ctime
from sys import exit
from os import getuid, path
from psb_conf import SCRIPT_DIR, LOG_FILENAME, REPORT_FILENAME, REPORT_PATH, INFO_FILENAME, REP_FILENAME, \
    MAC_SQL_UPGRADE, MAC_SQL_TRANSACTION, \
    MIC_SQL_UPGRADE, MIC_SQL_TRANSACTION, \
    ACL_SQL_UPGRADE, ACL_SQL_TRANSACTION, \
    DEFAULT_SCALE_FACTOR, DEFAULT_TRANSACTIONS, DEFAULT_THREADS, \
    SCALE_FACTOR, SCALE_FACTOR_STEP, LIMITE_SCALE_FACTOR, \
    TRANSACTIONS, TRANSACTIONS_STEP, LIMITE_TRANSACTIONS, \
    THREADS, THREADS_STEP, LIMITE_THREADS, \
    CLIENTS, CLIENTS_STEP, LIMITE_CLIENTS, STEP_RATIO_BY_CLIENTS, PG_VERSION, DATA_SYSMON_FILENAME, \
    TANTOR_VERSION, VENV_PATH, PG_VERSION_18
from libs.libpsqltests import Test, OLAPTest
from libs.zefir import UploaderZC
from libs.libpsb import astra_version, dump, upload_results_to_ftp
from libs.libtable import Report
from libs.libsysmon import create_avgsysmon_filereport, sorted_data_from_sysmonfile


DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-t', '--testlist',
                    action='store',
                    choices=['base'],  # 'mac', 'mic', 'acl']
                    required=False,
                    default='base',
                    help='testlist',
                    dest='TEST_LIST')

parser.add_argument('-m', '--mode',
                    action='store',
                    choices=['default',
                             'extended'],
                    required=False,
                    default='default',
                    help='Get default parameters (default) or parameters from config (extended)',
                    dest='MODE')

parser.add_argument('-sn', '--stand-num',
                    action='store',
                    choices=['1',
                             '3',
                             '4'],
                    required=True,
                    help='stand num',
                    dest='STAND')

parser.add_argument('-db', '--dbprep',
                    action='store_true',
                    required=False,
                    help='prepare host, create cluster, init database ...',
                    dest='DB_PREPARE')

parser.add_argument('-c', '--cleaner',
                    action='store_true',
                    required=False,
                    help='delete cluster, delete database ...',
                    dest='CLEANER')

parser.add_argument('-sm', '--system-monitor',
                    action='store_true',
                    required=False,
                    dest='SYSMON')

parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

parser.add_argument('-tk', '--token',
                    action='store',
                    required=False,
                    default=None,
                    help='confluence access token',
                    dest='TOKEN')

parser.add_argument('-cs', '--confluence-space',
                    action='store',
                    required=True,
                    help='confluence space',
                    dest='SPACE')

parser.add_argument('-cpp', '--confluence-parent-page',
                    action='store',
                    required=True,
                    help='confluence parent page',
                    dest='PPAGE')

parser.add_argument('-cnp', '--confluence-new-page',
                    action='store',
                    required=True,
                    help='confluence new page',
                    dest='NPAGE')

parser.add_argument('-pack', '--package',
                    action='store',
                    required=True,
                    help='test package',
                    dest='PACKAGE')

parser.add_argument('-fti', '--folder-tree-id',
                    action='store',
                    required=True,
                    help='folder-tree-id',
                    dest='FTI')

parser.add_argument('-tcyc', '--test-cycle-name',
                    action='store',
                    required=True,
                    help='test-cycle-name',
                    dest='TCYC')

parser.add_argument('-tcas', '--test-case-name',
                    action='store',
                    required=True,
                    help='test-case-name',
                    dest='TCAS')

parser.add_argument('-ba', '--basic-auth',
                    action='store',
                    required=True,
                    help='basic-auth',
                    dest='BA')

parser.add_argument('-tcv', '--test-cycle-version',
                    action='store',
                    required=True,
                    help='test-cycle-version',
                    dest='TCV')

parser.add_argument('-psql_aud',
                    action='store',
                    required=False,
                    help='auditoff arg',
                    dest='AUDIT_OFF')

parser.add_argument('-parsec',
                    action='store',
                    required=False,
                    help='parsec mode',
                    dest='PARSEC')

parser.add_argument('-psql_van',
                    action='store',
                    required=False,
                    help='postgresql vanilla',
                    dest='PSQL_VANILLA')

parser.add_argument('-sd',
                    action='store',
                    required=False,
                    help='activate alternative SDA storage',
                    dest='SD')

parser.add_argument('-tantor_van',
                    action='store',
                    required=False,
                    help='tantor vanilla',
                    dest='TANTOR_VANILLA')

# TODO
parser.add_argument('-olap',
                    action='store',
                    required=False,
                    help='olap',
                    dest='OLAP')



args = parser.parse_args()


uzs = UploaderZC(folder_tree_id=args.FTI,
                test_cycle_name=args.TCYC,
                test_case_name=args.TCAS,
                basic_auth=args.BA,
                test_cycle_version=args.TCV,
                token=args.TOKEN,
                username=args.USER)
uzs.upload_test_cycle_status('progress')

#
start_time = time()

# is root?
if getuid() != 0:
    exit(2)

# clean log
if path.exists(LOG_FILENAME):
    report = open(LOG_FILENAME, 'w')
    report.close()

if path.exists(INFO_FILENAME):
    report = open(INFO_FILENAME, 'w')
    report.close()

# create dir
if not path.exists(REPORT_PATH):
    os.mkdir(REPORT_PATH, mode=0o755)

version = astra_version()
print(args.NPAGE)

if args.SD:
    alt_storage = 'SDA'
else: alt_storage = None

if args.DB_PREPARE:
    '''
        Настроить машину, инициализировать тестовую БД
    '''
    if args.AUDIT_OFF:
        subprocess.run('sudo bash {dir}/psb_db_prep_stand{stand}.sh {init_file} {aud_off} {ast}'.format(dir=SCRIPT_DIR,
                                                                                                        stand=args.STAND,
                                                                                                        init_file='psb_init.sql',
                                                                                                        aud_off='audit_off',
                                                                                                        ast=alt_storage),
                                                                                                        shell=True,
                                                                                                        stderr=subprocess.DEVNULL)
    elif args.PARSEC:
        subprocess.run(f'sudo bash {SCRIPT_DIR}/psb_db_prep_stand{args.STAND}_parsec.sh {alt_storage}',
                       shell=True,
                       stderr=subprocess.DEVNULL)
    elif args.PSQL_VANILLA:
        subprocess.run(f'sudo bash {SCRIPT_DIR}/psb_db_prep_stand{args.STAND}.sh psb_init.sql vanilla {alt_storage}',
                       shell=True,
                       stderr=subprocess.DEVNULL)
    elif args.TANTOR_VANILLA:
        subprocess.run(f'sudo bash {SCRIPT_DIR}/psb_tantordb_prep.sh tantor {args.STAND} {alt_storage}',
                       shell=True,
                       stderr=subprocess.DEVNULL)
    # TODO 
    elif args.OLAP == "heavy_queries":
        subprocess.run(f'sudo bash {SCRIPT_DIR}/psb_db_prep_stand{args.STAND}_olap.sh {alt_storage}',
                       shell=True,
                       stderr=subprocess.DEVNULL)
    else:
        if 'debian' in args.NPAGE:
            subprocess.run('sudo bash {dir}/psb_db_prep_stand{stand}.sh {init_file} {deb}'.format(dir=SCRIPT_DIR,
                                                                                                stand=args.STAND,
                                                                                                init_file='psb_init.sql',
                                                                                                deb='debian'),
                                                                                                shell=True,
                                                                                                stderr=subprocess.DEVNULL)
        else:
            subprocess.run('sudo bash {dir}/psb_db_prep_stand{stand}.sh {init_file} {ast}'.format(dir=SCRIPT_DIR,
                                                                                                stand=args.STAND,
                                                                                                init_file='psb_init.sql',
                                                                                                ast=alt_storage),
                                                                                                shell=True,
                                                                                                stderr=subprocess.DEVNULL)

if args.TEST_LIST == 'base':
    if args.MODE == 'default':

        '''
            Запуск на оптимальных настройках
        '''
        scale_factor = DEFAULT_SCALE_FACTOR
        transactions = DEFAULT_TRANSACTIONS
        threads = DEFAULT_THREADS
        clients = CLIENTS
        clients_step = CLIENTS_STEP
        step_ratio_by_clients = STEP_RATIO_BY_CLIENTS  # (client_step)*(step_ratio_by_clients) every iteration
        limite_clients = LIMITE_CLIENTS

        # clean conf
        report = open(REPORT_FILENAME, 'w')
        report.close()

        print('# INFO # --- scale factor {}'.format(str(scale_factor)))
        print('# INFO # --- transactions count {}'.format(str(transactions)))
        print('# INFO # --- threads count {}'.format(str(threads)))
        print('# INFO # --- max clients count {}'.format(str(limite_clients)))

        if args.SYSMON:
            sysmon = subprocess.Popen(f"{VENV_PATH} psb_sysmon.py", shell=True)

        # TODO OLAP test
        if args.OLAP == "heavy_queries":
            test = OLAPTest()
            milti_result = asyncio.run(test.run_test())
            print("milti_result:", milti_result)
        else:
            while clients <= limite_clients:
                print('# INFO # --- clients count {}'.format(str(clients)))
                with open(REPORT_FILENAME, 'a+') as report_file:
                    report_file.write(str(clients))
                if 'debian' in args.NPAGE:
                    test = Test(scale=scale_factor,
                                trs=transactions,
                                ths=threads,
                                cls=clients,
                                debian=True)
                elif args.PARSEC:
                    test = Test(scale=scale_factor,
                                trs=transactions,
                                ths=threads,
                                cls=clients,
                                parsec=True)
                elif args.TANTOR_VANILLA:
                    test = Test(scale=scale_factor,
                                trs=transactions,
                                ths=threads,
                                cls=clients,
                                tantor=True)
                else: 
                    test = Test(scale=scale_factor,
                                trs=transactions,
                                ths=threads,
                                cls=clients)
                print(test.run_test())
                if args.SYSMON:
                    file_sysmon = open(f'{DATA_SYSMON_FILENAME}', 'a+')
                    file_sysmon.write("-----\n")
                    file_sysmon.close()
                clients += clients_step
                clients_step *= step_ratio_by_clients

        if args.SYSMON:
            sysmon.kill()
            create_avgsysmon_filereport()
            data, x = sorted_data_from_sysmonfile()
            report = Report(param_name='clients', sysmon=True)
            report.create_beauty_table()
            report.create_psb_cl_la_graph()
            report.create_psb_cl_tps1_graph()
            report.create_psb_cl_tps2_graph()
            report.create_psb_cl_tpsall_graph()
            report.create_sysmon_graph(x=x,
                                       y=data[0],
                                       filename='psb_load_cpu',
                                       title_graph='Load CPU',
                                       y_label="CPU %",
                                       x_rlim=x[-1])

            report.create_sysmon_graph(x=x,
                                       y=data[1],
                                       filename='psb_load_memory',
                                       title_graph='Load memory',
                                       y_label='Memory %',
                                       x_rlim=x[-1])

            report.create_sysmon_graph(x=x,
                                       y=data[2],
                                       filename='psb_load_psqlmemory',
                                       title_graph='Load PSQL memory',
                                       y_label='Memory %',
                                       x_rlim=x[-1])

            report.create_sysmon_graph(x=x,
                                       y=data[3],
                                       filename='psb_load_disk',
                                       title_graph='Load disk',
                                       y_label='Disk %',
                                       x_rlim=x[-1])
            report.merge(table_lst=['psb_report_table.html'],
                         graph_lst=['psb_clients_la_graph.png',
                                    'psb_clients_tps1_graph.png',
                                    'psb_clients_tps2_graph.png',
                                    'psb_clients_tpsall_graph.png',
                                    'psb_load_cpu.png',
                                    'psb_load_memory.png',
                                    'psb_load_psqlmemory.png',
                                    'psb_load_disk.png'])
            report.create_tar()
        # TODO OLAP report
        elif args.OLAP == "heavy_queries":
            pass
        # TODO OLAP report
        # ---------------------------
        else: # create report
            report = Report(param_name='clients')
            report.create_beauty_table()
            report.create_psb_cl_la_graph()
            report.create_psb_cl_tps1_graph()
            report.create_psb_cl_tps2_graph()
            report.create_psb_cl_tpsall_graph()
            report.merge(table_lst=['psb_report_table.html'],
                         graph_lst=['psb_clients_la_graph.png',
                                    'psb_clients_tps1_graph.png',
                                    'psb_clients_tps2_graph.png',
                                    'psb_clients_tpsall_graph.png'])
            report.create_tar()

    # if args.MODE == 'extended':
    #     '''
    #         Проверка на втроенных сценариях.
    #         Нахождение предельного коэффициента масштаба.
    #     '''
    #     report = open(REPORT_FILENAME, 'w')
    #     report.close()

    #     scale_factor = SCALE_FACTOR
    #     scale_factor_step = SCALE_FACTOR_STEP
    #     limite_scale_factor = LIMITE_SCALE_FACTOR
    #     max_scale_factor = 0

    #     while scale_factor < limite_scale_factor:
    #         with open(REPORT_FILENAME, 'a+') as report_file:
    #             report_file.write(str(scale_factor))
    #         test = Test(scale=scale_factor)
    #         result = test.run_test()
    #         if result is False:
    #             max_scale_factor = scale_factor
    #             break
    #         else:
    #             max_scale_factor = scale_factor
    #             scale_factor += scale_factor_step
    #             print(result)

    #     # create report
    #     report = Report(param_name='scale')
    #     report.create_beauty_table('psb_scale_table.html')
    #     report.create_psb_sc_la_graph()
    #     report.create_psb_sc_tpsall_graph()

    #     '''
    #         Проверка на втроенных сценариях.
    #         Нахождение предельного числа транзакций.
    #     '''
    #     report = open(REPORT_FILENAME, 'w')
    #     report.close()

    #     transactions = TRANSACTIONS
    #     transactions_step = TRANSACTIONS_STEP
    #     limite_transactions = LIMITE_TRANSACTIONS
    #     max_transactions_count = 0

    #     while transactions < limite_transactions:
    #         with open(REPORT_FILENAME, 'a+') as report_file:
    #             report_file.write(str(transactions))
    #         test = Test(trs=transactions)
    #         result = test.run_test()
    #         if result is False:
    #             max_transactions_count = transactions
    #             break
    #         else:
    #             max_transactions_count = transactions
    #             transactions += transactions_step
    #             print(result)

    #     # create report
    #     report = Report(param_name='transactions')
    #     report.create_beauty_table('psb_transactions_table.html')
    #     report.create_psb_tr_la_graph()
    #     report.create_psb_tr_tpsall_graph()

    #     '''
    #         Проверка на втроенных сценариях.
    #         Нахождение предельного числа потоков. 
    #     '''
    #     report = open(REPORT_FILENAME, 'w')
    #     report.close()

    #     threads = THREADS
    #     threads_step = THREADS_STEP
    #     limite_threads = LIMITE_THREADS
    #     max_threads_count = 0

    #     while threads < limite_threads:
    #         with open(REPORT_FILENAME, 'a+') as report_file:
    #             report_file.write(str(threads))
    #         test = Test(ths=threads)
    #         result = test.run_test()
    #         if result is False:
    #             max_threads_count = threads
    #             break
    #         else:
    #             max_threads_count = threads
    #             threads += threads_step
    #             print(result)

    #     # create report
    #     report = Report(param_name='threads')
    #     report.create_beauty_table('psb_threads_table.html')
    #     report.create_psb_th_la_graph()
    #     report.create_psb_th_tpsall_graph()

    #     '''
    #         Проверка на втроенных сценариях.
    #         Нахождение предельного числа клиентов. 
    #     '''
    #     report = open(REPORT_FILENAME, 'w')
    #     report.close()

    #     clients = CLIENTS
    #     clients_step = CLIENTS_STEP
    #     limite_clients = LIMITE_CLIENTS
    #     max_clients_count = 0

    #     while clients < limite_clients:
    #         with open(REPORT_FILENAME, 'a+') as report_file:
    #             report_file.write(str(clients))
    #         test = Test(cls=clients)
    #         result = test.run_test()
    #         if result is False:
    #             max_clients_count = clients
    #             break
    #         else:
    #             max_clients_count = clients
    #             clients += clients_step
    #             print(result)

    #     # create report
    #     report = Report(param_name='clients')
    #     report.create_beauty_table('psb_clients_table.html')
    #     report.create_psb_cl_la_graph()
    #     report.create_psb_cl_tpsall_graph()

    #     print('# INFO # --- max scale factor {}'.format(str(max_scale_factor)))
    #     print('# INFO # --- max transactions count {}'.format(str(max_transactions_count)))
    #     print('# INFO # --- max threads count {}'.format(str(max_threads_count)))
    #     print('# INFO # --- max clients count {}'.format(str(max_clients_count)))

    #     '''
    #         Запуск на максимально допустимых настройках
    #     '''
    #     report = open(REPORT_FILENAME, 'w')
    #     report.close()

    #     scale_factor = SCALE_FACTOR
    #     scale_factor_step = SCALE_FACTOR_STEP
    #     transactions = TRANSACTIONS
    #     transactions_step = TRANSACTIONS_STEP
    #     threads = THREADS
    #     threads_step = THREADS_STEP
    #     clients = CLIENTS
    #     clients_step = CLIENTS_STEP

    #     while (scale_factor <= max_scale_factor) and \
    #             (transactions <= max_transactions_count) and \
    #             (threads <= max_threads_count) and \
    #             (clients <= max_clients_count):
    #         with open(REPORT_FILENAME, 'a+') as report_file:
    #             report_file.write(str(scale_factor))
    #             report_file.write(str(transactions))
    #             report_file.write(str(threads))
    #             report_file.write(str(clients))
    #         test = Test(scale=scale_factor,
    #                     trs=transactions,
    #                     ths=threads,
    #                     cls=clients)
    #         print(test.run_test())
    #         scale_factor += scale_factor_step
    #         transactions += transactions_step
    #         threads += threads_step
    #         clients += clients_step

    #     report = Report(all_params=True)
    #     report.create_beauty_table('psb_max_table.html')
    #     report.merge(table_lst=['psb_scale_table.html',
    #                             'psb_transactions_table.html',
    #                             'psb_threads_table.html',
    #                             'psb_clients_table.html',
    #                             'psb_max_table.html'],
    #                  graph_lst=['psb_scale_la_graph.png',
    #                             'psb_scale_tpsall_graph.png',
    #                             'psb_transactions_la_graph.png',
    #                             'psb_transactions_tpsall_graph.png',
    #                             'psb_threads_la_graph.png',
    #                             'psb_threads_tpsall_graph.png',
    #                             'psb_clients_la_graph.png',
    #                             'psb_clients_tpsall_graph.png'])

print('# INFO # --- создаем dump БД')
dump()

if args.CLEANER:
    '''
        Удалить тестовую базу и настройки PostgreSQL
    '''
    subprocess.run('bash {dir}/psb_db_del.sh {v}'.format(dir=SCRIPT_DIR, v=version[0]),
                   shell=True,
                   stderr=subprocess.DEVNULL)

lead_time = strftime("%H:%M:%S", gmtime(time() - start_time))
print('lead time: {t}'.format(t=lead_time))

# собрать системную информацию   
al_version = astra_version()[0] 
if str(al_version).startswith('1.8'):
    if args.PSQL_VANILLA:
        psql_version = 16
    else: psql_version = PG_VERSION_18
elif str(al_version).startswith('1.7'):
    psql_version = PG_VERSION
else: psql_version = PG_VERSION
if args.TANTOR_VANILLA:
    info_lst = ['{digit_v}({mode})\n'.format(digit_v=astra_version()[0], mode=astra_version()[1]),
                subprocess.run('uname -r',
                            shell=True,
                            stdout=subprocess.PIPE).stdout.decode("utf-8"),
                subprocess.run("dpkg -l tantor-se-server-"+str(TANTOR_VERSION)+" | awk '{print $3}' | tail -n1",
                           shell=True,
                           stdout=subprocess.PIPE).stdout.decode("utf-8"),
                str(lead_time)]
else:
    info_lst = ['{digit_v}({mode})\n'.format(digit_v=astra_version()[0], mode=astra_version()[1]),
                subprocess.run('uname -r',
                            shell=True,
                            stdout=subprocess.PIPE).stdout.decode("utf-8"),
                subprocess.run("dpkg -l postgresql-"+str(psql_version)+" | awk '{print $3}' | tail -n1",
                            shell=True,
                            stdout=subprocess.PIPE).stdout.decode("utf-8"),
                str(lead_time)]

with open(INFO_FILENAME, 'a+') as info:
    info.writelines(info_lst)

#upload_results_to_ftp(args.TCV, f'{REPORT_FILENAME}', f'postgresql_{args.TCYC}_{REP_FILENAME}')

# public = Public(username=args.USER,
#                 token=args.TOKEN,
#                 conf_space=args.SPACE,
#                 conf_parent_page=args.PPAGE,
#                 conf_new_page_name=args.NPAGE,
#                 grade_stand=args.STAND,
#                 package=args.PACKAGE)

# public.run_publish()

package_name = 'postgresql-' + str(psql_version)

public_args = {
        'username':args.USER,
        'token':args.TOKEN,
        'conf_space':args.SPACE,
        'conf_parent_page':args.PPAGE,
        'conf_new_page_name':args.NPAGE,
        'grade_stand':args.STAND,
        'package':package_name,
        'folder_tree_id':args.FTI,
        'test_cycle_name':args.TCYC,
        'test_case_name':args.TCAS,
        'basic_auth':args.BA,
        'test_cycle_version':args.TCV
    }

if args.SD:
    public_args['storage'] = 'sas'
else:
    public_args['storage'] = 'nvme'

    
with open('psb_public_args.json', 'w') as w:
    json.dump(public_args, w)
