import array
import psutil
import sys
import os
import argparse
from libs.zefir import ZefirResultTable, ZefirStatusAPI
from libs.libovf import response
from time import ctime, sleep


parser = argparse.ArgumentParser()
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

parser.add_argument('-ovf',
                    action='store',
                    required=False,
                    help='overflow',
                    dest='OVF')

parser.add_argument('-check',
                    action='store',
                    required=False,
                    help='check',
                    dest='CHECK')
args = parser.parse_args()


# RAM overflow
def fill_memory():
    counter = 0
    try:
        memory_blocks = []
        while True:
            memory_blocks.append(array.array('B', [0] * (1024 * 1024)))  # Создание массива размером 1 МБ
            print(psutil.virtual_memory().percent)
            if psutil.virtual_memory().percent >= 100:
                counter += 1
                if counter == 1000:
                    with open('/home/u/test_err.log', 'w') as w:
                        w.write('System dont drop app')
                    sys.exit(5)

    except MemoryError as e:
        print(f"Memory overflow or encountered an error: {e}")


def check_drop():
    if not os.path.isdir('/home/u/test_err.log'):
        print('System success drop app')
        def upload_result_status():
            zefir = ZefirStatusAPI(folder_tree_id=args.FTI,
                                    test_cycle_name=args.TCYC,
                                    test_case_name=args.TCAS,
                                    basic_auth=args.BA)
            zefir.upload_status(91)

            zefir_table = ZefirResultTable(test_cycle_version=args.TCV,
                                            token=args.TOKEN,
                                            basic_auth=args.BA,
                                            username=args.USER)
            zefir_table
            return 0

        end_status = 0
        while end_status == 0:
            jira_end, life_end = response()
            try:
                if jira_end == 200 and life_end == 200:
                    if upload_result_status() == 0:
                        end_status += 1
                else: 
                    with open('JIRA_ERROR.log', 'a') as err:
                        err.write('end:\n')
                        err.write(ctime())
                        err.write(f'jira_status = {jira_end}\nlife_status = {life_end}')
                        err.write('---------' * 25)
                        err.write('\n\n')
                    sleep(60)
            except Exception as e:
                with open('JIRA_ERROR.log', 'a') as err:
                    err.write('end:\n')
                    err.write(ctime())
                    err.write(str(e))
                    err.write('---------' * 25)
                    err.write('\n\n')
                    end_status += 1

    else: 
        print('Fail: system didnt drop app')
        def upload_result_status():
            zefir = ZefirStatusAPI(folder_tree_id=args.FTI,
                                    test_cycle_name=args.TCYC,
                                    test_case_name=args.TCAS,
                                    basic_auth=args.BA)
            zefir.upload_status(92)

            zefir_table = ZefirResultTable(test_cycle_version=args.TCV,
                                            token=args.TOKEN,
                                            basic_auth=args.BA,
                                            username=args.USER)
            zefir_table
            return 0

        end_status = 0
        while end_status == 0:
            jira_end, life_end = response()
            try:
                if jira_end == 200 and life_end == 200:
                    if upload_result_status() == 0:
                        end_status += 1
                else: 
                    with open('JIRA_ERROR.log', 'a') as err:
                        err.write('end:\n')
                        err.write(ctime())
                        err.write(f'jira_status = {jira_end}\nlife_status = {life_end}')
                        err.write('---------' * 25)
                        err.write('\n\n')
                    sleep(60)
            except Exception as e:
                with open('JIRA_ERROR.log', 'a') as err:
                    err.write('end:\n')
                    err.write(ctime())
                    err.write(str(e))
                    err.write('---------' * 25)
                    err.write('\n\n')
                    end_status += 1



# SD overflow
def fill_disk():
    with open("/fill_disk_test.txt", "w") as f:
        f.write("")
    try:
        while True:
            print(psutil.disk_usage("/").percent)
            with open("/fill_disk_test.txt", "a") as f:
                f.write(f"{'Заполняем жесткий диск' * 2048}")
    except IOError as e:
        print(f"Возникла ошибка: {e}")
    
    print("Тестирование завершено!")
    print(psutil.disk_usage("/"))
    def upload_result_status():
        zefir = ZefirStatusAPI(folder_tree_id=args.FTI,
                                test_cycle_name=args.TCYC,
                                test_case_name=args.TCAS,
                                basic_auth=args.BA)
        zefir.upload_status(92)

        zefir_table = ZefirResultTable(test_cycle_version=args.TCV,
                                        token=args.TOKEN,
                                        basic_auth=args.BA,
                                        username=args.USER)
        zefir_table
        return 0

    end_status = 0
    while end_status == 0:
        jira_end, life_end = response()
        try:
            if jira_end == 200 and life_end == 200:
                if upload_result_status() == 0:
                    end_status += 1
            else: 
                with open('JIRA_ERROR.log', 'a') as err:
                    err.write('end:\n')
                    err.write(ctime())
                    err.write(f'jira_status = {jira_end}\nlife_status = {life_end}')
                    err.write('---------' * 25)
                    err.write('\n\n')
                sleep(60)
        except Exception as e:
            with open('JIRA_ERROR.log', 'a') as err:
                err.write('end:\n')
                err.write(ctime())
                err.write(str(e))
                err.write('---------' * 25)
                err.write('\n\n')
                end_status += 1



def jira_start():
    def test_cycle_status_start():
        zefir = ZefirStatusAPI(folder_tree_id=args.FTI,
                                test_cycle_name=args.TCYC,
                                test_case_name=args.TCAS,
                                basic_auth=args.BA)
        zefir.upload_status(90)
        zefir_table = ZefirResultTable(test_cycle_version=args.TCV,
                                        token=args.TOKEN,
                                        basic_auth=args.BA,
                                        username=args.USER)
        zefir_table

    start_status = 0
    while start_status == 0:
        jira_start, life_start = response()
        try:
            if jira_start == 200 and life_start == 200:
                test_cycle_status_start()
                start_status += 1
            else: 
                with open('JIRA_ERROR.log', 'a') as err:
                    err.write('start:\n')
                    err.write(ctime())
                    err.write(f'jira_status = {jira_start}\nlife_status = {life_start}')
                    err.write('---------' * 25)
                    err.write('\n\n')
                sleep(60)
        except Exception as e:
            with open('JIRA_ERROR.log', 'a') as err:
                err.write('start:\n')
                err.write(ctime())
                err.write(str(e))
                err.write('---------' * 25)
                err.write('\n\n')
                start_status += 1



#Start
if args.OVF == 'ram':
    jira_start()
    fill_memory()
elif args.OVF == 'sd':
    jira_start()
    fill_disk()

if args.CHECK == 'drop':
    check_drop()
elif args.CHECK == 'reboot':
    jira_start()

