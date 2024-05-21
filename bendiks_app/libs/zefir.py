#!/home/u/python/Python-3.12.1/venv/bin/python3.12

import json
import logging
import requests
import pandas as pd
from datetime import datetime
from collections import defaultdict
from numpy import where
import warnings
from sys import exit
from atlassian import Confluence
from os import remove, path
import re
from backup_image_conf import(test_run_stands,
                              test_run_modes,
                              tests_case_zefir_key,
                              testcase_orel_low_stand3,
                              testcase_orel_middle_stand4,
                              testcase_smolensk_low_stand3,
                              testcase_smolensk_middle_stand4,
                              testname_columns,
                              JIRA_URL,
                              CONFLUENCE_URL,
                              startswith_kernel_list)



def response_status():
    jira = requests.get(f'https://{JIRA_URL}').status_code
    life = requests.get(f'https://{CONFLUENCE_URL}').status_code
    return jira, life


class ZefirStatusAPI:

    def __init__(self,
                 folder_tree_id=None,
                 basic_auth=None,
                 test_cycle_name=None,
                 test_case_name=None,
                 result_code=None):

        '''
        Тут нужно указать ID папки дерева с прогонами, например (stress_test/1.7.4)
        Узнать ID можно в devtools

        folder_id_1.7.4 = 2773
        folder_id_1.7.3 = 2774
        folder_id_1.7.3.UU.1 = 2745

        __folder_tree_id = 2745
        __basic = BASIC_PASS
        __test_cycle_name = '1.7.3.UU.1_smolensk_5.15.0-33-generic_stand3'
        __test_case_name = 'auditd benchmark. psaud'
        __result_code = 89

                Статусы:
        92 - Провалено
        91 - Выполнено
        90 - Выполняется
        89 - Не запускался
        '''

        logging.basicConfig(filename='zefir.log',
                            filemode="a",
                            level=logging.INFO,
                            format='%(levelname)s: t:%(created)f th:%(thread)d ps:%(process)d <%(name)s> | %(message)s')
        self.logger = logging.getLogger()
        self.__folder_tree_id = folder_tree_id
        self.__basic = basic_auth
        self.__test_cycle_name = test_cycle_name
        self.__test_case_name = test_case_name
        self.__result_code = result_code

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
            'authority': JIRA_URL,
            'Authorization': self.__basic,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': f'https://{JIRA_URL}/secure/Tests.jspa',
            'X-Requested-With': 'XMLHttpRequest',
            'jira-project-id': '11200',
            'Connection': 'keep-alive',
            #'Cookie': '_ga=GA1.2.1097806090.1675253155; _ym_uid=1675343686577278045; _ym_d=1675343686; ajs_user_id=fc7aec5884ea4e02056a8ce8f996001f49ce373e; ajs_anonymous_id=cf669b37-814c-498d-a98c-8e36022b80b9; _gid=GA1.2.366592657.1683015166; INGRESSCOOKIE=1128ad0bc2c3007521086fda6d56670d|99e0851c823ae836b8f684b10f61e1b8; crowd.token_key=A2xWy4WrFlZg-50YPj8yJQAAAAABBoABZHRpbW9uaW4; JSESSIONID=2F8197402877646EA13D49452656855F; atlassian.xsrf.token=BCF3-299N-BY94-II8F_3a78239741b52a11e8873fd208433d4794b07b95_lin; seraph.rememberme.cookie=91479%3Aad54b720c0af146e9da927247f4dbe0f33f429ce',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'TE': 'trailers'
        }

        if path.isfile('zefir.log'):
            remove('zefir.log')

    #Тест кейсы в прогоне
    '-------------------------------------------------------------------------------------------------------------------------------'
    def test_case_dates(self, test_cycle_id):
        self.url_test_cycle = f'''https://{JIRA_URL}/rest/tests/1.0/testrun/{test_cycle_id}/testrunitems?fields=id,
                            index,issueCount,$lastTestResult
                        '''
        self.response_test_cycle = requests.get(self.url_test_cycle, headers=self.headers)

        test_case_id = [self.response_test_cycle.json()['testRunItems'][x]['$lastTestResult']['id'] for x \
                        in range(len(self.response_test_cycle.json()['testRunItems']))]
        test_case_name = [self.response_test_cycle.json()['testRunItems'][x]['$lastTestResult']['testCase']['name'] for x \
                        in range(len(self.response_test_cycle.json()['testRunItems']))]
        test_case_result_status = [self.response_test_cycle.json()['testRunItems'][x]['$lastTestResult']['testResultStatusId'] for x \
                                in range(len(self.response_test_cycle.json()['testRunItems']))]
        self.logger.info(test_case_id)
        self.logger.info(test_case_name)
        self.logger.info(test_case_result_status)
        self.logger.info('------' * 30)
        return test_case_id, test_case_name, test_case_result_status
    '-------------------------------------------------------------------------------------------------------------------------------'

    def upload_status(self, result_status):
        url = f'https://{JIRA_URL}/rest/tests/1.0/testresult'
        headers1 = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
            'authority': JIRA_URL,
            'Authorization': self.__basic,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': f'https://{JIRA_URL}/secure/Tests.jspa',
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/json;charset=utf-8',
            'jira-project-id': '11200',
            'Origin': f'https://{JIRA_URL}',
            'Connection': 'keep-alive',
            #'Cookie': '_ga=GA1.2.1097806090.1675253155; _ym_uid=1675343686577278045; _ym_d=1675343686; ajs_user_id=fc7aec5884ea4e02056a8ce8f996001f49ce373e; ajs_anonymous_id=cf669b37-814c-498d-a98c-8e36022b80b9; _gid=GA1.2.366592657.1683015166; INGRESSCOOKIE=1128ad0bc2c3007521086fda6d56670d|99e0851c823ae836b8f684b10f61e1b8; crowd.token_key=YSx8wZ-ooDdVIOnv8HyUqQAAAAABBoABZHRpbW9uaW4; JSESSIONID=4D2E62E6FC8E90D1821B702723E10B38; atlassian.xsrf.token=BCF3-299N-BY94-II8F_05e76f3ef04efbf6fd1d219fe74aef7ba313b8b2_lin; seraph.rememberme.cookie=91744%3A1cc82d680915e49453fb80ba5c55bd6e44a504f7',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'TE': 'trailers'
        }
        '''
        Статусы:
        92 - Провалено
        91 - Выполнено
        90 - Выполняется
        89 - Не запускался
        '''
        now = datetime.utcnow()
        iso_date = now.isoformat() + 'Z'

        payload = [{"id": self.finder(), "testResultStatusId": result_status, "userKey": "JIRAUSER38882",
                "executionDate": iso_date}]#, "actualStartDate": "2023-04-26T16:47:23.475Z"}]
        self.logger.info(payload)
        self.logger.info('------' * 30)

        json_payload = json.dumps(payload)
        response = requests.put(url, headers=headers1, data=json_payload)
        print(response.status_code)

    def dates_test_cycle(self):
        url_list_test_cycles = f'''https://{JIRA_URL}/rest/tests/1.0/testrun/search?fields=id,key,name,folderId,iterationId,
                                projectVersionId,environmentId,userKeys,environmentIds,plannedStartDate,plannedEndDate,executionTime,
                                estimatedTime,testResultStatuses,testCaseCount,issueCount,status(id,name,i18nKey,color),
                                customFieldValues,createdOn,createdBy,updatedOn,updatedBy,
                                owner&query=testRun.projectId+IN+(11200)+AND+testRun.folderTreeId+IN+({self.__folder_tree_id})+ORDER+
                                BY+testRun.name+ASC&maxResults=400&startAt=0&archived=false
                            '''
        response_list_test_cycles = requests.get(url_list_test_cycles, headers=self.headers)
        data = response_list_test_cycles.json()
        self.logger.info(data)
        self.logger.info('------' * 30)

        test_cycle = {}
        test_cycle['test_cycle_id'] = [data['results'][i]['id'] for i in range(len(data['results']))]
        test_cycle['test_cycle_name'] = [data['results'][i]['name'] for i in range(len(data['results']))]
        source_dict = test_cycle
        self.test_cycle_dates = []

        for id, name in zip(source_dict['test_cycle_id'], source_dict['test_cycle_name']):
            self.test_cycle_dates.append({'test_cycle_id': id, 'test_cycle_name': name})
        for x in range(len(self.test_cycle_dates)):
            self.test_cycle_dates[x]['test_case_id'] = self.test_case_dates(self.test_cycle_dates[x]['test_cycle_id'])[0]
            self.test_cycle_dates[x]['test_case_name'] = self.test_case_dates(self.test_cycle_dates[x]['test_cycle_id'])[1]
            self.test_cycle_dates[x]['test_case_result_status'] = self.test_case_dates(self.test_cycle_dates[x]['test_cycle_id'])[2]

        self.logger.info(self.test_cycle_dates)
        self.logger.info('------' * 30)
        return self.test_cycle_dates

    def finder(self):
        self.dates_test_cycle()
        for x in range(len(self.test_cycle_dates)):
            if self.test_cycle_dates[x]['test_cycle_name'] == self.__test_cycle_name:
                index_tc_name = self.test_cycle_dates[x]['test_case_name'].index(self.__test_case_name)
                self.logger.info(self.test_cycle_dates[x]['test_case_id'][index_tc_name])
                self.logger.info('------' * 30)
                return self.test_cycle_dates[x]['test_case_id'][index_tc_name]
            
    #Run
    '''
    upload_status(finder(__test_cycle_name, __test_case_name), __result_code)
    '''




class ZefirResultTable:

    def __init__(self,
                 token=None,
                 basic_auth=None,
                 username=None,
                 test_cycle_version=None):
        #Устанавливаем путь к таблице
        '''
        Для скачанной вручную excel таблицы 
        '''
        # path_home = os.environ['HOME'] + '/Загрузки'
        # result_file = [file for file in os.listdir(path_home) if file.startswith('test-results')]
        # try:
        #     result_file_path = path_home + '/' + result_file[0]
        #     with open('result_file_path.txt', 'w') as w:
        #         w.write(result_file_path)
        # except IndexError:
        #     print('Файл с исходной таблицей не обнаружен')
        #     exit(1)

        #Конвертируем таблицу в удобный для обработки вид
        # with warnings.catch_warnings(record=True):
        #     warnings.simplefilter('default')
        #     df = pd.read_excel(result_file_path, engine='openpyxl')
        # df = df[['Test Cycle.Name', 'Test Case.Name', 'Execution.Result']]
        # df['Test Cycle.Name'] = df['Test Cycle.Name'].str.replace('_', ' ')
        # df['Test Cycle.Name'] = [x.split(' ') for x in df['Test Cycle.Name']]    
        # df = df[['Test Cycle.Name', 'Test Case.Name', 'Execution.Result']].T
        # df_to_list = [x for x in df.columns for x in df[x]]
        # dates_list = [df_to_list[x:x+3] for x in range(0, len(df_to_list), 3)]
        # print(dates_list)
        '''
        --------------------------------------------------------------------------------------------------------------------------
        '''
        self.__token = token
        self.__username = username
        self.__basic = basic_auth
        self.__pt_version = test_cycle_version

        check_len_version = self.__pt_version.split('.')
        if len(check_len_version) == 4 and check_len_version[3] != 'UU':
            release_version = '.'.join(check_len_version[:3])
            rc_version = self.__pt_version
            filter_url = f"'%2Fstress_test%2F{release_version}%2F{rc_version}%2F**'"
        elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
            release_version = '.'.join(check_len_version[:5])
            rc_version = self.__pt_version
            filter_url = f"'%2Fstress_test%2F{release_version}%2F{rc_version}%2F**'"
        else:
            filter_url = f'%27%2Fstress_test%27,%27%2Fstress_test%2F{self.__pt_version}%27'


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
            'Authorization': self.__basic,
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

        #Создаем фрейм с первоначальными данными
        data = defaultdict(list)
        data['Версия'] = [dates_list[0][0][0]]
        data['Ядро'] = [dates_list[0][0][2]]
        data['Режим'] = [dates_list[0][0][1]]
        data['№ стенда'] = [dates_list[0][0][3]]
        self.new_tab = pd.DataFrame(data=data)

        #Заполняем новый фрейм данными из таблицы
        def add_columns_rows(iter):
            '''
            Функция добавляет столбец при совпадении элементов в первой паре словаря и 
            добавляет строку при совпадении элементов второй пары словаря или 
            несовпадении в первой паре 
            '''
            global new_tab 
            if [dates_list[iter][0][0]] == list(data.values())[0] and [dates_list[iter][0][2]] == list(data.values())[1] \
            and [dates_list[iter][0][1]] == list(data.values())[2] and [dates_list[iter][0][3]] == list(data.values())[3]:
                if dates_list[iter][1] in self.new_tab.columns:
                    self.new_tab.at[self.new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
                else:
                    self.new_tab.insert(loc=len(self.new_tab.columns), column=dates_list[iter][1], value='')
                    self.new_tab.at[self.new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
            else:
                data['Версия'] = [dates_list[iter][0][0]]
                data['Ядро'] = [dates_list[iter][0][2]]
                data['Режим'] = [dates_list[iter][0][1]]
                data['№ стенда'] = [dates_list[iter][0][3]]
                self.new_tab = self.new_tab._append(data, ignore_index=True)
                if dates_list[iter][1] in self.new_tab.columns:
                   self.new_tab.at[self.new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
                else:
                    self.new_tab.insert(loc=len(self.new_tab.columns), column=dates_list[iter][1], value='')
                    self.new_tab.at[self.new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
        [add_columns_rows(item) for item in range(0, len(dates_list))]
        
        #Наводим красоту
        self.new_tab.fillna('', inplace=True)
        columns = ['Версия', 'Ядро', 'Режим', '№ стенда']
        for col in columns:
            self.new_tab[col] = self.new_tab[col].astype(str).str.replace(r'\[|\]|\'', '', regex=True)
        
        for k, v in testname_columns.items():
            self.new_tab.rename(columns={k:v}, inplace=True)
        for name in self.new_tab.columns:
            self.new_tab[name] = where(self.new_tab[name] == 'NOT_EXECUTED', 'Не запускался', self.new_tab[name])
            self.new_tab[name] = where(self.new_tab[name] == 'IN_PROGRESS', 'Выполняется', self.new_tab[name])
            self.new_tab[name] = where(self.new_tab[name] == 'PASS', 'Выполнено', self.new_tab[name])
            self.new_tab[name] = where(self.new_tab[name] == 'FAIL', 'Провалено', self.new_tab[name])

        self.new_tab = self.new_tab.sort_values(by=['Режим', '№ стенда'], ascending=[True, True])
        self.new_tab = self.new_tab[[x for x in self.new_tab if x not in self.new_tab.columns[4:].sort_values()] 
                        + [x for x in self.new_tab.columns[4:].sort_values() if x in self.new_tab]]
        self.new_tab = self.new_tab.T
        self.new_tab.to_html('res.html', index=False)

        #Создаем новую html страницу
        with open('res.html', 'r') as r:
            html_table = r.readlines()
        with open('./templates/stand.html', 'r') as r:
            stand = r.read()
        with open('./templates/times.html', 'r') as r:
            times = r.read()
        def write_html(string):
            with open('result.html', 'a') as w:
                w.write(string)

        write_html(f'<h1>Прогресс выполнения тестового прогона {dates_list[0][0][0]}</h1>')
        for string in html_table:
            if string == '      <td>Выполняется</td>\n':
                write_html(string.replace(string, '      <td style="background-color:#fffacf;">Выполняется</td>\n'))
            elif string == '      <td>Не запускался</td>\n':
                write_html(string.replace(string, '      <td style="background-color:#f8f8f8;">Не запускался</td>\n'))
            elif string == '      <td>Выполнено</td>\n':
                write_html(string.replace(string, '      <td style="background-color:#dafee6;">Выполнено</td>\n'))
            elif string == '      <td>Провалено</td>\n':
                write_html(string.replace(string, '      <td style="background-color:#feffa2; color:#fe1313;">Провалено</td>\n'))
            elif string == '      <td>orel</td>\n':
                write_html(string.replace(string, '      <td style="background-color:#e4f1fc;">orel</td>\n'))
            elif string == '      <td>smolensk</td>\n':
                write_html(string.replace(string, '      <td style="background-color:#ffe8e8;">smolensk</td>\n'))
            else: write_html(string)
        write_html(stand)
        write_html(times)

        #Выкладываем на лайф
        confluence = Confluence(url=f'https://{CONFLUENCE_URL}',
                                username=self.__username,
                                token=self.__token)

        with open('result.html', 'r') as r:
            table = r.read()
        #print(table)


        def upload_page(space, 
                        title, 
                        name_page:str, 
                        body):
            
            check_len_version = self.__pt_version.split('.')
            if len(check_len_version) == 4 and check_len_version[3] != 'UU':
                release_version = '.'.join(check_len_version[:3])
                rc_version = self.__pt_version
                if not confluence.page_exists(space=space, title=f'STRESS_stp ⬝ {release_version}'):
                    parent_id = confluence.get_page_id(space=space, title=title)
                    confluence.create_page(space=space, parent_id=parent_id, title=f'STRESS_stp ⬝ {release_version}', body='')
                    
                if not confluence.page_exists(space=space, title=rc_version):
                    parent_id = confluence.get_page_id(space=space, title=f'STRESS_stp ⬝ {release_version}')
                    confluence.create_page(space=space, parent_id=parent_id, title=rc_version, body=body)
                else: 
                    page_id = confluence.get_page_id(space=space, title=rc_version)
                    confluence.update_page(page_id=page_id, title=rc_version, body=body)

            elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
                release_version = '.'.join(check_len_version[:5])
                rc_version = self.__pt_version
                if not confluence.page_exists(space=space, title=f'STRESS_stp ⬝ {release_version}'):
                    parent_id = confluence.get_page_id(space=space, title=title)
                    confluence.create_page(space=space, parent_id=parent_id, title=f'STRESS_stp ⬝ {release_version}', body='')
                    
                if not confluence.page_exists(space=space, title=rc_version):
                    parent_id = confluence.get_page_id(space=space, title=f'STRESS_stp ⬝ {release_version}')
                    confluence.create_page(space=space, parent_id=parent_id, title=rc_version, body=body)
                else: 
                    page_id = confluence.get_page_id(space=space, title=rc_version)
                    confluence.update_page(page_id=page_id, title=rc_version, body=body)

            else:
                if not confluence.page_exists(space=space, title=name_page):
                    parent_id = confluence.get_page_id(space=space, title=title)
                    confluence.create_page(space=space, parent_id=parent_id, title=name_page, body=body)
                else: 
                    page_id = confluence.get_page_id(space=space, title=name_page)
                    confluence.update_page(page_id=page_id, title=name_page, body=body)
                

        upload_page('DEVQA', 'Состав тестового прогона', f'STRESS_stp ⬝ {self.__pt_version}', table)

        if path.isfile('res.html'):
            remove('res.html')
        if path.isfile('result.html'):
            remove('result.html')
        #with open('result_file_path.txt', 'r') as r:
        #    rfp = r.read()
        #if path.isfile(rfp):
        #    remove(rfp)

        


class ZefirTestRun:

    def __init__(self,
                 use_kernels=None,
                 stands=None,
                 release=None,
                 rc=False,
                 kernel_repo=None,
                 kernel_vers=None):
        


        self.JIRA_URL = f'https://{JIRA_URL}'
        self.headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0'}
        with open('/home/u/tokens.json', 'r') as r:
            tokens = json.load(r)
        self.__username = tokens['username']
        self.__jira_token = tokens['jira_token']
        self.PROJECT_NAME = "BT"
        if release != None:
            self.release = ''.join(release)
        if rc != False:
            self.rc = ''.join(rc)
        else: self.rc = rc
        #self.kernel_vers = kernel_vers
        #self.kernel_repo = kernel_repo
        self.use_kernels = use_kernels
        #self.testcase_orel = testcase_orel
        #self.testcase_orel_stand2 = testcase_orel_stand2
        #self.testcase_smolensk = testcase_smolensk
        #self.testcase_smolensk_stand2 = testcase_smolensk_stand2
        self.testcase_orel_low_stand3 = testcase_orel_low_stand3
        self.testcase_smolensk_low_stand3 = testcase_smolensk_low_stand3
        self.testcase_orel_middle_stand4 = testcase_orel_middle_stand4
        self.testcase_smolensk_middle_stand4 = testcase_smolensk_middle_stand4
        self.stands = stands
        self.modes = test_run_modes
        self.tests_case_zefir_key = tests_case_zefir_key



    def get_kernels_from_file(self):
        kern_packages_list = []
        kernels = []

        if path.isfile('available_version'):
            with open('available_version', 'r') as r:
                repsponse_vers = r.read()

            vers_pattern = re.compile(r'Version: \d.*', re.IGNORECASE)
            vers_release = re.search(vers_pattern, repsponse_vers)

        if path.isfile('available_packages'):
            with open('available_packages', 'r') as r:
                packages_list = r.read()
            
            for package in packages_list.split('\n'):
                if 'Package: linux-image' in package:
                    package = package.replace("Package: ", "")
                    kern_packages_list.append(package)
            for kern_package in kern_packages_list:
                pattern_generic = re.compile(r'linux-image-\d\.\d*\.\d*-\d*-generic\D*', re.IGNORECASE)
                pattern_lowlatency = re.compile(r'linux-image-\d\.\d*\.\d*-\d*-lowlatency\D*', re.IGNORECASE)
                #pattern_hardened = re.compile(r'linux-image-\d\.\d*\.\d*-\d*-hardened\D*', re.IGNORECASE)
                generic_kernel = re.findall(pattern_generic, kern_package)
                lowlatency_kernel = re.findall(pattern_lowlatency, kern_package)
                
                if generic_kernel:
                    kernels.append(str(generic_kernel).replace("[", "").replace("]", "").replace("'", "").replace("linux-image-", ""))
                if lowlatency_kernel:
                    kernels.append(str(lowlatency_kernel).replace("[", "").replace("]", "").replace("'", "").replace("linux-image-", ""))

            kernel_list = [kernel for kernel in kernels if any(kernel.startswith(x) for x in startswith_kernel_list)]

            print(vers_release[0])
            print(kernel_list)

            if path.isfile('available_packages'):
                remove('available_packages')
            if path.isfile('available_version'):
                remove('available_version')
            
            return vers_release[0], kernel_list



    # Создание тестового прогона
    def create_test_run(self, bearer_token, kernel, cases, assignee, release, mode, stand, rc=False):
        if rc:
            testrun_name = f'{rc}_{mode}_{kernel}_{stand}'
            folder_name = f'/stress_test/{release}/{rc}'
        else: 
            testrun_name = f'{release}_{mode}_{kernel}_{stand}'
            folder_name = f'/stress_test/{release}'

        is_env_empty = False
        headers = {"Authorization":bearer_token}
        jira_user_request_url = self.JIRA_URL + "/rest/api/2/user?username=" + assignee
        jira_user_response = requests.get(jira_user_request_url, headers=headers)
        jira_user_id = jira_user_response.json()["key"]

        # Формируем строку в JSON формате с указанием проекта, имени прогона, списка тест-кейсов и задачей в DEVQA    
        items = [f'{{"testCaseKey":"{case}", "environment":"{kernel}", "assignedTo":"{jira_user_id}"}}' for case in cases]
        testrun_body = f'{{"projectKey":"{self.PROJECT_NAME}", "folder":"{folder_name}", "name":"{testrun_name}", "items":[{",".join(items)}]}}'
        testrun_body = json.loads(testrun_body)

        # Выполняем POST запрос на создание тестового прогона и проверяем ответ сервера
        # (ожидается HTTP-код 201 - успешно создано)
        request_url = self.JIRA_URL + "/rest/atm/1.0/testrun/"
        print("Sending request to create testrun and link it to jira task.")
        print("Request_URL: " + str(request_url))
        #print("Testrun_body: " + str(testrun_body))
        response = requests.post(request_url, json=testrun_body, headers=headers)
        environment_text = 'was not found for field environment on project BT'
        if response.status_code == 400 and str(response.json()).find(environment_text, 0, len(str(response.json()))) != -1:
            print(f'Kernel {str(kernel)} was not found for field environment on project BT')
            is_env_empty = True       
            items = [f'{{"testCaseKey":"{case}", "assignedTo":"{jira_user_id}"}}' for case in cases]
            testrun_body = f'{{"projectKey":"{self.PROJECT_NAME}", "folder":"{folder_name}", "name":"{testrun_name}", "items":[{",".join(items)}]}}'
            testrun_body = json.loads(testrun_body)
            request_url = self.JIRA_URL + "/rest/atm/1.0/testrun/"

            print("Trying to create testrun with empty environment field.")
            print("Request_URL: " + str(request_url))
            #print("Testrun_body: " + str(testrun_body))
            response = requests.post(request_url, json=testrun_body, headers=headers)
        if response.status_code == 201:
            testrun_url = self.JIRA_URL + "/secure/Tests.jspa#/testCycle/" + response.json().get('key')
            if is_env_empty:
                result = "Успешно создан тестовый прогон " + testrun_url 
            else:
                result = "Успешно создан тестовый прогон " + testrun_url 
            print(result)
        else:
            print("Failed to create testrun. Response: " + str(response.status_code))
            print(response.text)



    def creater(self):
        for kernel in self.use_kernels:
            for stand in self.stands:
                for mode in self.modes:
                    if mode == 'orel' and stand == 'stand3':
                        self.create_test_run(bearer_token=self.__jira_token, 
                                             kernel=kernel, 
                                             cases=[self.tests_case_zefir_key[test] for test in self.testcase_orel_low_stand3],
                                             assignee=self.__username,
                                             release=self.release,
                                             mode=mode,
                                             stand=stand,
                                             rc=self.rc)
                    elif mode == 'orel' and stand == 'stand4':
                        self.create_test_run(bearer_token=self.__jira_token, 
                                             kernel=kernel, 
                                             cases=[self.tests_case_zefir_key[test] for test in self.testcase_orel_middle_stand4],
                                             assignee=self.__username,
                                             release=self.release,
                                             mode=mode,
                                             stand=stand,
                                             rc=self.rc)
                    elif mode == 'smolensk' and stand == 'stand3':
                        self.create_test_run(bearer_token=self.__jira_token, 
                                             kernel=kernel, 
                                             cases=[self.tests_case_zefir_key[test] for test in self.testcase_smolensk_low_stand3],
                                             assignee=self.__username,
                                             release=self.release,
                                             mode=mode,
                                             stand=stand,
                                             rc=self.rc)
                    elif mode == 'smolensk' and stand == 'stand4':
                        self.create_test_run(bearer_token=self.__jira_token, 
                                             kernel=kernel, 
                                             cases=[self.tests_case_zefir_key[test] for test in self.testcase_smolensk_middle_stand4],
                                             assignee=self.__username,
                                             release=self.release,
                                             mode=mode,
                                             stand=stand,
                                             rc=self.rc)

                    # if mode == 'orel' and stand != 'stand2':
                    #     self.create_test_run(bearer_token=self.__jira_token, 
                    #                          kernel=kernel, 
                    #                          cases=[self.tests_case_zefir_key[test] for test in self.testcase_orel],
                    #                          assignee=self.__username,
                    #                          release=self.release,
                    #                          mode=mode,
                    #                          stand=stand,
                    #                          rc=self.rc)
                    # elif mode == 'orel' and stand == 'stand2':
                    #     self.create_test_run(bearer_token=self.__jira_token, 
                    #                          kernel=kernel, 
                    #                          cases=[self.tests_case_zefir_key[test] for test in self.testcase_orel_stand2],
                    #                          assignee=self.__username,
                    #                          release=self.release,
                    #                          mode=mode,
                    #                          stand=stand,
                    #                          rc=self.rc)
                    # elif mode == 'smolensk' and stand != 'stand2':
                    #     self.create_test_run(bearer_token=self.__jira_token, 
                    #                          kernel=kernel, 
                    #                          cases=[self.tests_case_zefir_key[test] for test in self.testcase_smolensk],
                    #                          assignee=self.__username,
                    #                          release=self.release,
                    #                          mode=mode,
                    #                          stand=stand,
                    #                          rc=self.rc)
                    # elif mode == 'smolensk' and stand == 'stand2':
                    #     self.create_test_run(bearer_token=self.__jira_token, 
                    #                          kernel=kernel, 
                    #                          cases=[self.tests_case_zefir_key[test] for test in self.testcase_smolensk_stand2],
                    #                          assignee=self.__username,
                    #                          release=self.release,
                    #                          mode=mode,
                    #                          stand=stand,
                    #                          rc=self.rc)
                    


