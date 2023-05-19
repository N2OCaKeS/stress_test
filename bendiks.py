#!/bin/python3

import subprocess
from backup_image_conf import branches, cycle_tree_index, tests
import requests
import json

with open('/home/timonin/tokens.json', 'r') as r:
    tokens = json.load(r)
__conf_token = tokens['conf_token']
__username = tokens['username']
__jira_token = tokens['jira_token']
__pt_version = '1.7.3.UU.2'

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



#dates_list = [[['1.7.4', 'orel', '5.10.176-1-generic', 'stand1'], 'postgresql benchmark', 'PASS'], [['1.7.4', 'orel', '5.15.0-70-generic', 'stand1'], 'file system benchmark. EXT4', 'NOT_EXECUTED'], [['1.7.4', 'orel', '5.15.0-70-generic', 'stand1'], 'file system benchmark. XFS', 'PASS'], [['1.7.4', 'orel', '5.15.0-70-generic', 'stand1'], 'postgresql benchmark', 'PASS'], [['1.7.4', 'orel', '5.15.0-70-lowlatency', 'stand1'], 'postgresql benchmark', 'PASS']]

for i in range(0, len(dates_list)):

    print(tests[dates_list[i][1]])

    sn = f'-sn {list(dates_list[i][0][3])[-1]}' 
    rs = f'-rs {dates_list[i][0][0]}'
    test = f'-test {tests[dates_list[i][1]]}' 
    mode = f'-mode {dates_list[i][0][1]}'
    kn = f'-kn {dates_list[i][0][2]}' 
    stand = f'-stand {dates_list[i][0][3]}'
    tcyc = f'-tcyc {"_".join(dates_list[i][0])}' 
    tcas = f'-tcas "{dates_list[i][1]}"' 
    branch = f'-branch {branches[dates_list[i][1]]}' 
    cti = f'-cti {cycle_tree_index[dates_list[i][0][0]]}'

    #print(f'{sn} {rs} {test} {mode} {kn} {stand} {tcyc} {tcas} {branch} {cti}')
    subprocess.run(f'./backup_image.py {sn} {rs} {test} {mode} {kn} {stand} {tcyc} {tcas} {branch} {cti}', shell=True)

