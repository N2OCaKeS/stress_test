#!/bin/python3

import subprocess
from backup_image_conf import branches, cycle_tree_index, tests, parent_page_list
import requests
import json
import argparse
import datetime

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
__test_list = ['XFS', 'EXT4', 'NTFS', 'EXT4 parsec', 'postgresql', 'postgresql-sm', 'auditd-p', 'auditd-u', 'auditd-f', 'syslog-ng']

#Делаем get запрос в jira
matrix_url = f'''https://jira.astralinux.ru/rest/tests/1.0/reports/testresults/matrix/testrun?displayUnit=COUNT&epicJQL=&jql=&
                period=MONTH&projectId=11200&scorecardOption=EXECUTION_RESULTS&tql=testResult.projectId+IN+(11200)+AND+testRun.
                folderName+IN+(%27%2Fstress_test%27,%27%2Fstress_test%2F{__pt_version}%27)&traceabilityCustomTreeDisplayOption=
                CONDENSED&traceabilityMatrixOption=COVERAGE_TEST_CASES&traceabilityReportOption=COVERAGE_TEST_CASES&traceability
                TreeOption=COVERAGE_TEST_CASES
                '''
headers = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
    'authority': 'jira.astralinux.ru',
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

tcd = '''
 WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW 
 WWWWWWNNNNNNNWWWWWWWWWWWWWWWWWWWWNNNWWWWWNxlcccclOWWWWWWWWNWWWWWWNXXXXXXXXXKKK00KNWWWWWW 
 WWWWWNO:'''',coOXNWWWWWWWWWWWN0xl; ;kNWWWNx.     :XWWWWWW0c;;:dXNo..''''........;0WWWWWW 
 WWWWWWK,    .,...:d0NWWWWWKkl,..',. .:0NWW0,     .dNWWWWW0'   lN0'      .........oNWWWWW 
 WWWWWWK;   .kNKx:. .,o0NOc.  .oOXNo.  .oXWX:      .kWWWWW0,   oW0oc.   :0KKKKKKOdxXWWWWW 
 WWWWWWK;   ,KWWWWx.   ,x;    oNWWWK,   .dWNo.      ,0WWWW0,   oWWW0'   lNWWWWWWWWNWWWWWW 
 WWWWWWK;   ;KWWWWO.   .l'   .dWWWWNo    :KNd,.   .. :KWWW0,   oNWW0'   lNWWWWWWWWWWWWWWW 
 WWWWWWX;   ;KWWWWO.   .c'   .dWWWWWk.   ,xl..    ;l. oNWW0,   oNWW0'   cNWWWWX0NWWWWWWWW 
 WWWWWWX;   ;KWWWWO.   .:.   .dWWWWWO'   .xc      :0c .xNW0,   oNWW0'   .;::::'cKWWWWWWWW 
 WWWWWWX:   ,KWWWWO.   .;.   .dWWWWW0'   .kd.     :X0, 'OW0,   oNWW0,    ..... ;KWWWWWWWW 
 WWWWWWX:   ,KWWWWO.   .;.   .dWWWWW0'   .kO.     ;XWk. :KK,   oNWWK,   ,OKKK0xxXWWWWWWWW 
 WWWWWWX:   ,KWWWWO.   .;.   .dWWWWW0,   .x0;   .,lKWNo  lO,   oWWWK,   ;KWWWWWWWWWWWWWWW 
 WWWWWWX:   ,0WWWWO.   .c'   .dWWWWW0,   .ol.   .xXNWWK; .c'   oWWWK,   ;KWWWWWWWWWWWWWWW 
 WWWWWWX:   ,0WWWWk.  .c0o.  .dWWWWW0,   .dl    .kWWWWWO.      oNXX0;   ;KWWWWWWWNKNWWWWW 
 WWWWWWX:   .kWWN0:  ;kNWNO:. ,ONWWXx.  .oXk.   .xWWWWWNd.     oWO;,.   .lddddddl;oXWWWWW 
 WWWWWWK;    .;c;..:kXWWWWWNOc..,c:'..;o0NWK;   'xWWWWWWX:     lNK;              .xWWWWWW 
 WWWWWNkc,,,,,,,:o0NWWWWWWWWWWKd:;:lx0NWWWWNo. ,kXWWWWWWNx;;;;;dXWklllllllllcc:;;oXWWWWWW 
 WWWWWWNNNNNWNNNWWWWWWWWWWWWWWWWWWWWWWWWWWWWk:.:XWWWWWWWWNWNNWWWWWWWWWWWWWWWWWWWNWWWWWWWW 
 WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWKkcoNWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW 
 WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWNXkkWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW 
'''

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
          
for i in range(0, len(dates_list)):  
    start_time = datetime.datetime.now().replace(microsecond=0)  
    print('-----' * 20)
    print('Итерация №', i + 1)
    print(f'Прогресс выполнения - {int((i + 1) * 100 / len(dates_list))}%')
    print('Время запуска:', start_time)
    if dates_list[i][0][3] == __stand:
        print(f'Cтенд: \033[92m{dates_list[i][0][3]}\033[0m')
        if tests[dates_list[i][1]] in __test_list:
            if args.KERNEL:
                if dates_list[i][0][2] == args.KERNEL:
                    print(f'Ядро: \033[92m{args.KERNEL}\033[0m')
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
                    cti = f'-cti {cycle_tree_index[dates_list[i][0][0]]}'
                    pp = f'-pp "{parent_page_list[__pt_version][tests[dates_list[i][1]]]}"'
                    testnum = f'-testnum {i + 1}'
                    psql = '-ps psql'
                    if tests[dates_list[i][1]] == 'auditd-p':
                        testlist = f'-aud psaud'
                    elif tests[dates_list[i][1]] == 'auditd-f':
                        testlist = f'-aud fileaud'
                    elif tests[dates_list[i][1]] == 'auditd-u':
                        testlist = f'-aud useraud'
                    print('Выполняется...')
                    #print(f'{sn} {rs} {test} {mode} {kn} {stand} {tcyc} {tcas} {branch} {cti} {pp}')
                    if tests[dates_list[i][1]] == 'postgresql' or tests[dates_list[i][1]] == 'postgresql-sm':
                        subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                    {tcas} {branch} {cti} {pp} {psql} {testnum}', shell=True)
                    elif tests[dates_list[i][1]].startswith('auditd'):
                        subprocess.run(f'./backup_image.py {testlist} {sn} {rs} {test} {mode} {kn} \
                                       {stand} {tcyc} {tcas} {branch} {cti} {pp} {testnum}', shell=True)
                    else: 
                        subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                        {tcas} {branch} {cti} {pp} {testnum}', shell=True)
                    end_time = datetime.datetime.now().replace(microsecond=0)
                    print('Выполнен')
                    print('Время завершения:', end_time)
                    print('Затрачено времени:', end_time - start_time)
                else: print(f'Ядро: \033[91m{dates_list[i][0][2]}\033[0m', 'игнорируется')
            else:
                print(f'Ядро: {dates_list[i][0][2]}')
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
                cti = f'-cti {cycle_tree_index[dates_list[i][0][0]]}'
                pp = f'-pp "{parent_page_list[__pt_version][tests[dates_list[i][1]]]}"'
                testnum = f'-testnum {i + 1}'
                psql = '-ps psql'
                if tests[dates_list[i][1]] == 'auditd-p':
                    testlist = f'-aud psaud'
                elif tests[dates_list[i][1]] == 'auditd-f':
                    testlist = f'-aud fileaud'
                elif tests[dates_list[i][1]] == 'auditd-u':
                    testlist = f'-aud useraud'
                print('Выполняется...')
                #print(f'{sn} {rs} {test} {mode} {kn} {stand} {tcyc} {tcas} {branch} {cti} {pp}')
                if tests[dates_list[i][1]] == 'postgresql' or tests[dates_list[i][1]] == 'postgresql-sm':
                    subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                {tcas} {branch} {cti} {pp} {psql} {testnum}', shell=True)
                elif tests[dates_list[i][1]].startswith('auditd'):
                    subprocess.run(f'./backup_image.py {testlist} {sn} {rs} {test} {mode} {kn} \
                                       {stand} {tcyc} {tcas} {branch} {cti} {pp} {testnum}', shell=True)
                else: 
                    subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} \
                                    {tcas} {branch} {cti} {pp} {testnum}', shell=True)
                end_time = datetime.datetime.now().replace(microsecond=0)
                print('Выполнен')
                print('Время завершения:', end_time)
                print('Затрачено времени:', end_time - start_time)
        else: print(f'Тест: \033[91m{tests[dates_list[i][1]]}\033[0m игнорируется')
    else: print('Cтенд:', dates_list[i][0][3], 'игнорируется')
print(f'\n\033[95m{tcd}\033[0m\n')
