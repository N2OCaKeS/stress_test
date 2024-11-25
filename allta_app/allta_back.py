#!/home/u/python/Python-3.12.1/venv/bin/python3.12

import subprocess
from allta_image_conf import branches, cycle_tree_index, tests, parent_page_list, JIRA_URL
from libs.libconfluence import SendCommentToConfluence
from libs.liballta import busy_status_control
import requests
import json
import argparse
import datetime
from time import sleep


parser = argparse.ArgumentParser()
parser.add_argument('-rs', '--release',
                    action='store',
                    required=True,
                    help='release num',
                    dest='RELEASE')

parser.add_argument('-st',
                    action='store',
                    required=True,
                    help='stand',
                    dest='STAND')

parser.add_argument('-kn',
                    action='store',
                    required=False,
                    help='kernel',
                    dest='KERNEL')

parser.add_argument('-ts',
                    action='store',
                    required=True,
                    help='test name(s)',
                    dest='TESTS')

args = parser.parse_args()

with open('/home/u/tokens.json', 'r') as r:
    tokens = json.load(r)
__conf_token = tokens['conf_token']
__username = tokens['username']
__jira_token = tokens['jira_token']

#__pt_version = '1.7.4'
__pt_version = args.RELEASE
#__stand = 'stand1'
__stand = args.STAND
#__test_list = ['XFS', 'EXT4', 'NTFS', 'EXT4 parsec', 'postgresql', 'postgresql-sm', 'auditd-p', 'auditd-u', 'auditd-f', 'syslog-ng', 'unix']
__test_list = eval(args.TESTS)
bot_file = f'/home/u/git/stress_test/allta_app/telegrambot/results_{args.STAND}.txt'
total_start_time = datetime.datetime.now().replace(microsecond=0) 

conf = SendCommentToConfluence(rc_name=__pt_version,
                               username=__username,
                               token=__conf_token)

check_len_version = __pt_version.split('.')
if len(check_len_version) == 4 and check_len_version[3] != 'UU':
    release_version = '.'.join(check_len_version[:3])
    rc_version = __pt_version
    filter_url = f"'%2Fstress_test%2F{release_version}%2F{rc_version}%2F**'"
elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
    release_version = '.'.join(check_len_version[:5])
    rc_version = __pt_version
    filter_url = f"'%2Fstress_test%2F{release_version}%2F{rc_version}%2F**'"
else:
    filter_url = f'%27%2Fstress_test%27,%27%2Fstress_test%2F{__pt_version}%27'


#Делаем get запрос в jira
matrix_url = f'''https://{JIRA_URL}/rest/tests/1.0/reports/testresults/matrix/testrun?displayUnit=COUNT&epicJQL=&jql=&
                period=MONTH&projectId=11200&scorecardOption=EXECUTION_RESULTS&tql=testResult.projectId+IN+(11200)+AND+testRun.
                folderName+IN+({filter_url})&traceabilityCustomTreeDisplayOption=
                CONDENSED&traceabilityMatrixOption=COVERAGE_TEST_CASES&traceabilityReportOption=COVERAGE_TEST_CASES&traceability
                TreeOption=COVERAGE_TEST_CASES
                '''
headers = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
    'authority': JIRA_URL,
    'Authorization': __jira_token,
    'accept': 'application/json, text/plain, */*'
}

response = requests.get(matrix_url, headers=headers)
matrix = response.json()
#with open('test.json', 'w') as w:
#    json.dump(matrix, w)
#with open('test.json', 'r') as r:
#    matrix = json.load(r)

#Конвертируем данные в удобный для обработки вид
def dates(index):
    name = [matrix[index]['testRuns'][x]['testRun']['name'].replace('_', ' ') for x in range(len((matrix[index])['testRuns']))]
    split_name = [x.split(' ') for x in name]
    test_case_name = matrix[index]['testCase']['name']
    status = [matrix[index]['testRuns'][x]['status']['i18nKey'].split('.')[2] for x in range(len((matrix[index])['testRuns']))]
    return [v for i in range(len(split_name)) for v in (split_name[i], test_case_name, status[i])]
dates_list_raw = [v for i in range(len(matrix)) for v in dates(i)]
dates_list = sorted([dates_list_raw[x:x+3] for x in range(0, len(dates_list_raw), 3)])


#subprocess.run('./backup_image.py -sn 1 -rs 1.7.4 -test XFS -mode orel -kn 5.15.0-70-generic -stand stand1 -tcyc 1.7.4_orel_5.15.0-70-generic_stand1 -tcas "file system benchmark. XFS" -branch file_systems -cti 2773', shell=True)


with open(f'conf/status_output_{args.STAND}.log', 'w') as w:
        w.write('')

def save_status_output(output):
    with open(f'conf/status_output_{args.STAND}.log', 'a') as w:
        w.write(output)

with open(f'conf/all_output_{args.STAND}.log', 'w') as w:
        w.write('')

def save_all_output(output):
    with open(f'conf/all_output_{args.STAND}.log', 'a') as w:
        w.write(output)

def bot_results(output):
     with open(bot_file, 'a') as bf:
          bf.write(output)


#dates_list = [[['1.7.4', 'orel', '5.10.176-1-generic', 'stand1'], 'postgresql benchmark', 'PASS'], [['1.7.4', 'orel', '5.15.0-70-generic', 'stand1'], 'file system benchmark. EXT4', 'NOT_EXECUTED'], [['1.7.4', 'orel', '5.15.0-70-generic', 'stand1'], 'file system benchmark. XFS', 'PASS'], [['1.7.4', 'orel', '5.15.0-70-generic', 'stand1'], 'postgresql benchmark', 'PASS'], [['1.7.4', 'orel', '5.15.0-70-lowlatency', 'stand1'], 'postgresql benchmark', 'PASS']]

if args.KERNEL:
    print(f'''
 ----------------------------------------------------------------------------------------------------
 ----------------------------------------------------------------------------------------------------
| \033[43mПараметры запуска:\033[0m
| \033[93mВыбран релиз: {__pt_version} \033[0m 
| \033[93mВыбран стенд: {__stand} \033[0m
| \033[93mВыбрано ядро: {args.KERNEL} \033[0m                                                                           
| \033[93mВыбраны тесты: {__test_list} \033[0m                                                                    
 ----------------------------------------------------------------------------------------------------
 ----------------------------------------------------------------------------------------------------
''')
else:
    print(f'''
 ----------------------------------------------------------------------------------------------------
 ----------------------------------------------------------------------------------------------------
| \033[43mПараметры запуска:\033[0m
| \033[93mВыбран релиз: {__pt_version} \033[0m 
| \033[93mВыбран стенд: {__stand} \033[0m                                                                           
| \033[93mВыбраны тесты: {__test_list} \033[0m                                                                     
 ----------------------------------------------------------------------------------------------------
 ----------------------------------------------------------------------------------------------------
''')
          
if args.KERNEL:
    save_all_output(f'''
 -----------------------------------
 -----------------------------------
| Параметры запуска:
| Выбран релиз: {__pt_version} 
| Выбран стенд: {__stand} 
| Выбрано ядро: {args.KERNEL}                                                                            
| Выбраны тесты: {__test_list}                                                                    
 -----------------------------------
 -----------------------------------
''')
else:
    save_all_output(f'''
 -----------------------------------
 -----------------------------------
| Параметры запуска:
| Выбран релиз: {__pt_version}
| Выбран стенд: {__stand}                                                                         
| Выбраны тесты: {__test_list}                                                                   
 ------------------------------------
 ------------------------------------
''')

if args.KERNEL:
    save_status_output(f'''
 -----------------------------------
 -----------------------------------
| Параметры запуска:
| Выбран релиз: {__pt_version} 
| Выбран стенд: {__stand} 
| Выбрано ядро: {args.KERNEL}                                                                            
| Выбраны тесты: {__test_list}                                                                    
 -----------------------------------
 -----------------------------------
''')
else:
    save_status_output(f'''
 -----------------------------------
 -----------------------------------
| Параметры запуска:
| Выбран релиз: {__pt_version}
| Выбран стенд: {__stand}                                                                         
| Выбраны тесты: {__test_list}                                                                   
 ------------------------------------
 ------------------------------------
''')

if args.KERNEL:
    bot_head = f'''
 --------------------------------
| Параметры запуска:
| Выбран релиз: {__pt_version} 
| Выбран стенд: {__stand} 
| Выбрано ядро: {args.KERNEL}                                                                            
| Выбраны тесты: {__test_list}                                                                    
 --------------------------------
'''
else:
    bot_head = f'''
 --------------------------------
| Параметры запуска:
| Выбран релиз: {__pt_version}
| Выбран стенд: {__stand}                                                                         
| Выбраны тесты: {__test_list}                                                                   
 --------------------------------
'''
    

try:          
    for i in range(0, len(dates_list)):  
        start_time = datetime.datetime.now().replace(microsecond=0)  
        busy_status_control(__stand, 'TestRunner')
        save_all_output('---------------\n')
        print('-----' * 20)
        save_all_output(f'Итерация № {i + 1}\n')
        print(f'Итерация № {i + 1}')
        save_all_output(f'Прогресс выполнения - {int((i + 1) * 100 / len(dates_list))}%\n')
        print(f'Прогресс выполнения - {int((i + 1) * 100 / len(dates_list))}%')
        #save_status_output(f'Прогресс выполнения - {int((i + 1) * 100 / len(dates_list))}%')
        save_all_output(f'Время запуска: {start_time}\n')
        #save_status_output(f'Время запуска: {start_time}\n')
        print(f'Время запуска: {start_time}\n')
        if dates_list[i][0][3] == __stand:
            print(f'Cтенд: \033[92m{dates_list[i][0][3]}\033[0m')
            save_all_output(f'Cтенд: {dates_list[i][0][3]}\n')
            if tests[dates_list[i][1]] in __test_list:
                if args.KERNEL:
                    if dates_list[i][0][2] == args.KERNEL:
                        save_all_output(f'Ядро: {args.KERNEL}\n')
                        print(f'Ядро: \033[92m{args.KERNEL}\033[0m')
                        save_all_output(f'Тест: {tests[dates_list[i][1]]}\n')
                        print(f'Тест: \033[92m{tests[dates_list[i][1]]}\033[0m')
                        kn = f'-kn {args.KERNEL}'
                        tcyc = f'-tcyc {dates_list[i][0][0]}_{dates_list[i][0][1]}_{args.KERNEL}_{dates_list[i][0][3]}'
                        sn = f'-sn {list(dates_list[i][0][3])[-1]}' 
                        rs = f'-rs {dates_list[i][0][0]}'
                        test = f'-test "{tests[dates_list[i][1]]}"' 
                        mode = f'-mode {dates_list[i][0][1]}'
                        stand = f'-stand {dates_list[i][0][3]}'
                        tcas = f'-tcas "{dates_list[i][1]}"' 
                        branch = f'-branch {branches[dates_list[i][1]]}' 
                        cti = f'-cti {cycle_tree_index()[dates_list[i][0][0]]}'
                        pp = f'-pp "{parent_page_list()[__pt_version][tests[dates_list[i][1]]]}"'
                        testnum = f'-testnum {i + 1}'
                        psql = '-ps psql'
                        psql_kern = '-db-kernels psql'
                        psql_parsec = '-psql-parsec parsec'
                        psql_aud_off = '-psql_aud off'
                        psql_vanilla = '-psql-vanilla pv'
                        psql_balance = '-psql-bl balance'
                        tantor_vanilla = '-tantor-vanilla tv'
                        tantor_kern = '-db-kernels tantor'
                        ram_ovf = '-ovf ram'
                        sd_ovf = '-ovf sd'
                        ipa_auth = '-ipa-auth ipa'
                        parsec_impact = '-parsec-impact impact'
                        parsec_impact_ao = '-parsec-impact-ao audit-off'
                        apache_rp = '-apache rp'
                        steal_time = '-lvirt stealtime'
                        steal_time_sm = '-lvirt stealtime_sm'
                        fio = '-lvirt fio'
                        vunixbench = '-lvirt unixbench'
                        vpp = '-lvirt pingpong'
                        if tests[dates_list[i][1]] == 'auditd-p':
                            testlist = f'-aud psaud'
                        elif tests[dates_list[i][1]] == 'auditd-f':
                            testlist = f'-aud fileaud'
                        elif tests[dates_list[i][1]] == 'auditd-u':
                            testlist = f'-aud useraud'
                        save_all_output('Выполняется...\n')
                        print('Выполняется...')
                        with open(f'conf/col3_body_{__stand}.conf', 'w') as w:
                            w.write(f'{dates_list[i][0][2]}_{tests[dates_list[i][1]]}')
                        #print(f'{sn} {rs} {test} {mode} {kn} {stand} {tcyc} {tcas} {branch} {cti} {pp}')
                        if tests[dates_list[i][1]] == 'postgresql' or tests[dates_list[i][1]] == 'postgresql-sm':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {psql} {testnum}', shell=True)
                        elif tests[dates_list[i][1]] == 'psql parsec':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {psql_parsec}', shell=True)
                        elif tests[dates_list[i][1]] == 'psql vanilla':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {psql_vanilla}', shell=True)
                        elif tests[dates_list[i][1]] == 'psql balance':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {psql_balance}', shell=True)
                        elif tests[dates_list[i][1]] == 'psql kernels':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {psql_kern}', shell=True)
                        elif tests[dates_list[i][1]] == 'tantor kernels':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {tantor_kern}', shell=True)
                        elif tests[dates_list[i][1]] == 'tantor vanilla':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {tantor_vanilla}', shell=True)
                        elif tests[dates_list[i][1]] == 'postgresql-aud-off':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {psql_aud_off}', shell=True)
                        elif tests[dates_list[i][1]] == 'RAM-overflow':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                    {tcas} {branch} {cti} {pp} {testnum} {ram_ovf}', shell=True)
                        elif tests[dates_list[i][1]] == 'SD-overflow':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                    {tcas} {branch} {cti} {pp} {testnum} {sd_ovf}', shell=True)
                        elif tests[dates_list[i][1]] == 'FreeIPA auth':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {ipa_auth}', shell=True)
                        elif tests[dates_list[i][1]] == 'parsec impact-fs':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {parsec_impact}', shell=True)                           
                        elif tests[dates_list[i][1]] == 'parsec impact-fs aud-off':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {parsec_impact_ao}', shell=True) 
                        elif tests[dates_list[i][1]] == 'apache-rp':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {apache_rp}', shell=True)
                        elif tests[dates_list[i][1]] == 'steal time':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {steal_time}', shell=True)
                        elif tests[dates_list[i][1]] == 'steal time-sm':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {steal_time_sm}', shell=True)
                        elif tests[dates_list[i][1]] == 'FIO':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {fio}', shell=True)  
                        elif tests[dates_list[i][1]] == 'vUnixBench':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {vunixbench}', shell=True) 
                        elif tests[dates_list[i][1]] == 'vPingPong':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {vpp}', shell=True) 
                        elif tests[dates_list[i][1]].startswith('auditd'):
                            subprocess.run(f'./backup_image.py {testlist} {sn} {rs} {test} {mode} {kn} \
                                        {stand} {tcyc} {tcas} {branch} {cti} {pp} {testnum}', shell=True)
                        else: 
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                            {tcas} {branch} {cti} {pp} {testnum}', shell=True)
                        end_time = datetime.datetime.now().replace(microsecond=0)
                        save_all_output('Выполнен\n')
                        print('Выполнен')
                        save_all_output(f'Время завершения: {end_time}\n')
                        #save_status_output(f'Время завершения: {end_time}\n')
                        print(f'Время завершения: {end_time}')
                        save_all_output(f'Затрачено времени: {end_time - start_time}\n')
                        print(f'Затрачено времени: {end_time - start_time}')
                        #save_status_output(f'Затрачено времени: {end_time - start_time}\n')
                    else: 
                        save_all_output(f'Ядро: {dates_list[i][0][2]} игнорируется\n')
                        print(f'Ядро: \033[91m{dates_list[i][0][2]}\033[0m игнорируется')
                else:
                    save_all_output(f'Ядро: {dates_list[i][0][2]}\n')
                    print(f'Ядро: {dates_list[i][0][2]}')
                    save_all_output(f'Тест: {tests[dates_list[i][1]]}\n')
                    print(f'Тест: \033[92m{tests[dates_list[i][1]]}\033[0m')
                    kn = f'-kn {dates_list[i][0][2]}'
                    tcyc = f'-tcyc {"_".join(dates_list[i][0])}'
                    sn = f'-sn {list(dates_list[i][0][3])[-1]}' 
                    rs = f'-rs {dates_list[i][0][0]}'
                    test = f'-test "{tests[dates_list[i][1]]}"' 
                    mode = f'-mode {dates_list[i][0][1]}'
                    stand = f'-stand {dates_list[i][0][3]}' 
                    tcas = f'-tcas "{dates_list[i][1]}"' 
                    branch = f'-branch {branches[dates_list[i][1]]}' 
                    cti = f'-cti {cycle_tree_index()[dates_list[i][0][0]]}'
                    pp = f'-pp "{parent_page_list()[__pt_version][tests[dates_list[i][1]]]}"'
                    testnum = f'-testnum {i + 1}'
                    psql = '-ps psql'
                    psql_kern = '-db-kernels psql'
                    psql_parsec = '-psql-parsec parsec'
                    psql_aud_off = '-psql_aud off'
                    psql_vanilla = '-psql-vanilla pv'
                    psql_balance = '-psql-bl balance'
                    tantor_vanilla = '-tantor-vanilla tv'
                    tantor_kern = '-db-kernels tantor'
                    ram_ovf = '-ovf ram'
                    sd_ovf = '-ovf sd'
                    ipa_auth = '-ipa-auth ipa'
                    parsec_impact = '-parsec-impact impact'
                    parsec_impact_ao = '-parsec-impact-ao audit-off'
                    apache_rp = '-apache rp'
                    steal_time = '-lvirt stealtime'
                    steal_time_sm = '-lvirt stealtime_sm'
                    fio = '-lvirt fio'
                    vunixbench = '-lvirt unixbench'
                    vpp = '-lvirt pingpong'
                    if tests[dates_list[i][1]] == 'auditd-p':
                        testlist = f'-aud psaud'
                    elif tests[dates_list[i][1]] == 'auditd-f':
                        testlist = f'-aud fileaud'
                    elif tests[dates_list[i][1]] == 'auditd-u':
                        testlist = f'-aud useraud'
                    save_all_output('Выполняется...\n')
                    print('Выполняется...')
                    with open(f'conf/col3_body_{__stand}.conf', 'w') as w:
                            w.write(f'{dates_list[i][0][2]}_{tests[dates_list[i][1]]}')
                    
                    if tests[dates_list[i][1]] == 'postgresql' or tests[dates_list[i][1]] == 'postgresql-sm':
                        subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                    {tcas} {branch} {cti} {pp} {psql} {testnum}', shell=True)
                    elif tests[dates_list[i][1]] == 'psql parsec':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {psql_parsec}', shell=True)
                    elif tests[dates_list[i][1]] == 'psql vanilla':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {psql_vanilla}', shell=True)
                    elif tests[dates_list[i][1]] == 'psql balance':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {psql_balance}', shell=True)
                    elif tests[dates_list[i][1]] == 'psql kernels':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {psql_kern}', shell=True)
                    elif tests[dates_list[i][1]] == 'tantor kernels':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {tantor_kern}', shell=True)
                    elif tests[dates_list[i][1]] == 'tantor vanilla':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {tantor_vanilla}', shell=True)
                    elif tests[dates_list[i][1]] == 'postgresql-aud-off':
                        subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                    {tcas} {branch} {cti} {pp} {testnum} {psql_aud_off}', shell=True)
                    elif tests[dates_list[i][1]] == 'RAM-overflow':
                        subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                    {tcas} {branch} {cti} {pp} {testnum} {ram_ovf}', shell=True)
                    elif tests[dates_list[i][1]] == 'SD-overflow':
                        subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                    {tcas} {branch} {cti} {pp} {testnum} {sd_ovf}', shell=True)
                    elif tests[dates_list[i][1]] == 'FreeIPA auth':
                            subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {ipa_auth}', shell=True)
                    elif tests[dates_list[i][1]] == 'parsec impact-fs':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {parsec_impact}', shell=True)
                    elif tests[dates_list[i][1]] == 'parsec impact-fs aud-off':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {parsec_impact_ao}', shell=True)
                    elif tests[dates_list[i][1]] == 'apache-rp':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {apache_rp}', shell=True)
                    elif tests[dates_list[i][1]] == 'steal time':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {steal_time}', shell=True) 
                    elif tests[dates_list[i][1]] == 'steal time-sm':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {steal_time_sm}', shell=True)
                    elif tests[dates_list[i][1]] == 'FIO':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {fio}', shell=True) 
                    elif tests[dates_list[i][1]] == 'vUnixBench':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {vunixbench}', shell=True)
                    elif tests[dates_list[i][1]] == 'vPingPong':
                             subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum} {vpp}', shell=True)  
                    elif tests[dates_list[i][1]].startswith('auditd'):
                        subprocess.run(f'./backup_image.py {testlist} {sn} {rs} {test} {mode} {kn} \
                                        {stand} {tcyc} {tcas} {branch} {cti} {pp} {testnum}', shell=True)
                    else: 
                        subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum}', shell=True)
                    end_time = datetime.datetime.now().replace(microsecond=0)
                    save_all_output('Выполнен\n')
                    print('Выполнен')
                    save_all_output(f'Время завершения: {end_time}\n')
                    #save_status_output(f'Время завершения: {end_time}\n')
                    print(f'Время завершения: {end_time}')
                    save_all_output(f'Затрачено времени: {end_time - start_time}\n')
                    print(f'Затрачено времени: {end_time - start_time}')
                    #save_status_output(f'Затрачено времени: {end_time - start_time}\n')
            else: 
                save_all_output(f'Тест: {tests[dates_list[i][1]]} игнорируется\n')
                print(f'Тест: \033[91m{tests[dates_list[i][1]]}\033[0m игнорируется')
        else: 
            save_all_output(f'Cтенд: {dates_list[i][0][3]} игнорируется\n')
            print(f'Cтенд: {dates_list[i][0][3]} игнорируется')
    #sleep(30)
    total_end_time = datetime.datetime.now().replace(microsecond=0)
    save_all_output(f'\nDONE\n')
    print(f'\n\033[95mDone\033[0m\n')
    with open(f'conf/work_status_{args.STAND}.conf', 'w') as wr:
            wr.write('Готово')
    bot_results('Прогон завершен')
    bot_results(bot_head)
    bot_results(f'Затрачено времени: {total_end_time - total_start_time}')
    conf.send_comment()
    busy_status_control(__stand, 'testrun done')
except Exception as e:
    print(e)
    with open(f'conf/work_status_{args.STAND}.conf', 'w') as wr:
        wr.write('Готово')
    bot_results('Прогон завершен исключением')
    bot_results(bot_head)
    bot_results(f'Затрачено времени: {total_end_time - total_start_time}')
    busy_status_control(__stand, 'testrun fail')

