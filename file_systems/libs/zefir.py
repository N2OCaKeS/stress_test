

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
from libs.libpublic import Public
from libs.libstatistics import FileSystemStatistics
from time import sleep, ctime
from libs.libfsb import response



class UploaderZC(Public, FileSystemStatistics):

    def __init__(self,
                 folder_tree_id=None,
                 test_cycle_name=None,
                 test_case_name=None,
                 basic_auth=None,
                 test_cycle_version=None,
                 token=None,
                 username=None,
                 conf_space=None,
                 conf_parent_page=None,
                 conf_new_page_name=None,
                 grade_stand=None,
                 package=None,
                 public=False,
                 statistics=False,
                 file_system=None,
                 test_set=None):

        self.FTI = folder_tree_id
        self.TCYC = test_cycle_name
        self.TCAS = test_case_name
        self.BA = basic_auth
        self.TCV = test_cycle_version
        self.CT = token
        self.UN = username
        self.CS = conf_space
        self.CPP = conf_parent_page
        self.CNPN = conf_new_page_name
        self.GS = grade_stand
        self.PKG = package
        self.public = public
        self.statistics = statistics
        self.file_system=file_system
        self.TSET = test_set

    def test_cycle_status_changer(self, status):

        if self.public == True:
            public = Public(username=self.UN,
                            token=self.CT,
                            conf_space=self.CS,
                            conf_parent_page=self.CPP,
                            conf_new_page_name=self.CNPN,
                            grade_stand=self.GS,
                            package=self.PKG,
                            file_system=self.file_system,
                            test_set=self.TSET,
                            test_cycle_version=self.TCV)
            public.run_publish()

        zefir = ZefirStatusAPI(folder_tree_id=self.FTI,
                               test_cycle_name=self.TCYC,
                               test_case_name=self.TCAS,
                               basic_auth=self.BA)

        if self.statistics == True:
            statistics = FileSystemStatistics(username=self.UN, 
                                              token=self.CT)
            statistics.update_statistics()

        if status == 'pass':
            status_code = 91
        elif status == 'fail':
            status_code = 92
        elif status == 'progress':
            status_code = 90
        zefir.upload_status(status_code)
        zefir_table = ZefirResultTable(test_cycle_version=self.TCV,
                                       token=self.CT,
                                       basic_auth=self.BA,
                                       username=self.UN)
        zefir_table
        return 0

    def upload_test_cycle_status(self, zefir_status):
        wait_time = 30 #Минут ожидания
        requests_frequency = 180 #Периодичность обращений к jira в секундах 
        status = 0
        except_counter = 0
        while status == 0:
            jira, life = response()
            try:
                if jira == 200 and life == 200:
                    if self.test_cycle_status_changer(zefir_status) == 0:
                        status += 1
                else: 
                    with open('JIRA_ERROR.log', 'a') as err:
                        err.write('start:\n')
                        err.write(str(ctime()) + '\n')
                        err.write(f'jira_status = {jira}\nlife_status = {life}')
                        err.write('---------' * 25)
                        err.write('\n\n')
                    except_counter += 1
                    sleep(requests_frequency)
                    if except_counter == wait_time * 60 / requests_frequency:
                        err.write(f'Except count = {except_counter}, aborted')
                        status += 1
            except Exception as e:
                with open('JIRA_ERROR.log', 'a') as err:
                    err.write('start:\n')
                    err.write(str(ctime()) + '\n')
                    err.write(f'Type: {type(e).__name__}, Message: {str(e)}')
                    err.write('---------' * 25)
                    err.write('\n\n')
                    except_counter += 1
                    sleep(requests_frequency)
                    if except_counter == wait_time * 60 / requests_frequency:
                        err.write(f'Except count = {except_counter}, aborted')
                        status += 1


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
            'authority': 'jira.astralinux.ru',
            'Authorization': self.__basic,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://jira.astralinux.ru/secure/Tests.jspa',
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
        self.url_test_cycle = f'''https://jira.astralinux.ru/rest/tests/1.0/testrun/{test_cycle_id}/testrunitems?fields=id,
                            index,issueCount,$lastTestResult
                        '''
        self.response_test_cycle = requests.get(self.url_test_cycle, headers=self.headers)

        # t = {"total":1,
        #      "testRunItems":[{"index":0,
        #                   "id":1059609,
        #                   "$lastTestResult":{"environmentId":325,
        #                                      "id":1086632,
        #                                      "assignedTo":"JIRAUSER38882",
        #                                      "userKey":"JIRAUSER38882",
        #                                      "testCase":{"componentId":"11124",
        #                                                  "name":"postgresql benchmark",
        #                                                  "precondition":"Более подробная информация <a href=\"https://wiki.astralinux.ru/display/qa/Stress-test.+postgresql_benchmark\">здесь</a><br />Внести в конфигурационный файл <strong>psb_conf.py </strong>необходимые данные",
        #                                                  "id":9854,
        #                                                  "majorVersion":1,
        #                                                  "projectId":11200,
        #                                                  "key":"BT-T7555",
        #                                                  "folderId":2281,
        #                                                  "priorityId":104,
        #                                                  "objective":"Произвести нагрузочное тестирование БД PostgreSQL с помощью набора скриптов <strong>postgresql_benchmark</strong>."
        #                                                  },
        #                                     "testResultStatusId":91
        #                                     },"issueCount":0
        #                     }],
        #  "testRunItemsWithNoPermission":0
        # }

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
        url = 'https://jira.astralinux.ru/rest/tests/1.0/testresult'
        headers1 = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
            'authority': 'jira.astralinux.ru',
            'Authorization': self.__basic,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://jira.astralinux.ru/secure/Tests.jspa',
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/json;charset=utf-8',
            'jira-project-id': '11200',
            'Origin': 'https://jira.astralinux.ru',
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
        url_list_test_cycles = f'''https://jira.astralinux.ru/rest/tests/1.0/testrun/search?fields=id,key,name,folderId,iterationId,
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

        # z = {'total': 3, 
        #  'maxResults': 40, 
        #  'results': [{'owner': 'JIRAUSER38882', 
        #               'estimatedTime': 9000000, 
        #               'updatedBy': 'JIRAUSER38882', 
        #               'userKeys': ['JIRAUSER38882'], 
        #               'environmentIds': [325.0], 
        #               'updatedOn': '2023-04-26T16:43:47.419Z', 
        #               'createdOn': '2023-04-19T19:28:05.070Z', 
        #               'issueCount': 0, 'plannedEndDate': 
        #               '2023-04-19T16:27:22.881Z', 
        #               'folderId': 2773, 
        #               'executionTime': 0, 
        #               'testCaseCount': 1, 
        #               'plannedStartDate': '2023-04-19T16:27:22.881Z', 
        #               'customFieldValues': [], 
        #               'createdBy': 'JIRAUSER38882', 
        #               'name': '1.7.4_orel_5.10.176-1-generic_stand1', 
        #               'id': 2370, 
        #               'testResultStatuses': {'90': 1.0}, 
        #               'key': 'BT-C2317', 
        #               'status': {'color': '#f0ad4e', 
        #                          'i18nKey': 'TEST_RUN.STATUS.IN_PROGRESS', 
        #                          'name': 'In Progress', 
        #                          'id': 101
        #                          }
        #              }]}

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

        # {'test_cycle_id': 2215, 
        #  'test_cycle_name': '1.7.3.UU.1_orel_5.10.142-1-generic_stand1', 
        #  'test_case_id': [997555, 997558, 997556, 997557, 997559, 997560, 997561], 
        #  'test_case_name': ['linux_system_benchmark. UnixBench', 
        #                     'file system benchmark. EXT4', 
        #                     'syslog-ng benchmark', 
        #                     'postgresql benchmark', 
        #                     'file system benchmark. NTFS', 
        #                     'file system benchmark. OCFS2', 
        #                     'file system benchmark. XFS'], 
        #  'test_case_result_status': [89, 91, 91, 91, 91, 91, 91]}
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
        matrix_url = f'''https://jira.astralinux.ru/rest/tests/1.0/reports/testresults/matrix/testrun?displayUnit=COUNT&epicJQL=&jql=&
                        period=MONTH&projectId=11200&scorecardOption=EXECUTION_RESULTS&tql=testResult.projectId+IN+(11200)+AND+testRun.
                        folderName+IN+({filter_url})&traceabilityCustomTreeDisplayOption=
                        CONDENSED&traceabilityMatrixOption=COVERAGE_TEST_CASES&traceabilityReportOption=COVERAGE_TEST_CASES&traceability
                        TreeOption=COVERAGE_TEST_CASES
                        '''
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
            'authority': 'jira.astralinux.ru',
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
        testname_columns = {'file system benchmark. EXT4':'FS_EXT4', 'file system benchmark. XFS':'FS_XFS', 
                            'file system benchmark. OCFS2':'FS_OCFS2', 'file system benchmark. NTFS':'FS_NTFS',
                            'auditd benchmark. psaud':'Auditd_psaud', 'linux_system_benchmark. UnixBench':'UnixBench',
                            'file system benchmark. EXT3':'FS_EXT3', 'file system benchmark. EXT2':'FS_EXT2',
                            'file system benchmark. Fat32':'FS_Fat32', 'syslog-ng benchmark':'Syslog-NG', 'postgresql benchmark':'PostgreSQL',
                            'file system benchmark. EXT4 parsec':'FS_EXT4_parsec', 'auditd benchmark. fileaud':'Auditd_fileaud',
                            'auditd benchmark. useraud':'Auditd_useraud', 'file system benchmark. OCFS2 parsec':'FS_OCFS2_parsec',
                            'postgresql benchmark smol':'PostgreSQL_smol', 'postgresql benchmark audit-off':'PSQL_audit-off',
                            'storage drive overflow':'SD_overflow', 'ram overflow':'RAM_overflow', 'file system benchmark. XFS parsec':'FS_XFS_parsec',
                            'postgresql benchmark parsec':'PSQL_parsec', 'postgresql benchmark vanilla':'PSQL_vanilla',
                            'tantor benchmark vanilla':'Tantor_vanilla', 'postgresql benchmark kernels':'PSQL_kernels',
                            'tantor benchmark kernels':'Tantor_kernels', 'linux_system_benchmark. UnixBench parsec':'UnixBench_parsec',
                            'postgresql benchmark balance':'PSQL_balance', 'freeipa authentication test':'FreeIPA_auth',
                            'Parsec impact fs benchmark':'Parsec_impact-fs'}
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
        confluence = Confluence(url='https://life.astralinux.ru',
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

        
