# # from collections import defaultdict
# # import pandas as pd
# # from backup_image_conf import testname_columns
# # from numpy import where

# # dates_list = [[['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXFAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT2', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT3', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT4', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. FAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. XFS', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'syslog-ng benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'FIO benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'Steal time', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'freeipa authentication test', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark balance', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark kernels', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark vanilla', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXFAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT2', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT3', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT4', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. FAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. XFS', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'syslog-ng benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'FIO benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'Steal time', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'freeipa authentication test', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark balance', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark kernels', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark vanilla', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'Apache_ReverseProxy', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'Parsec impact fs benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'Parsec impact fs benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'auditd benchmark. fileaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'auditd benchmark. psaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'auditd benchmark. useraud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT4 parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'file system benchmark. XFS parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark smol', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'Apache_ReverseProxy', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'Parsec impact fs benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'Parsec impact fs benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'auditd benchmark. fileaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'auditd benchmark. psaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'auditd benchmark. useraud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT4 parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'file system benchmark. XFS parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark smol', 'NOT_EXECUTED']]

# # data = defaultdict(list)
# # data['Версия'] = [dates_list[0][0][0]]
# # data['Ядро'] = [dates_list[0][0][2]]
# # data['Режим'] = [dates_list[0][0][1]]
# # data['№ стенда'] = [dates_list[0][0][3]]
# # new_tab = pd.DataFrame(data=data)

# # #Заполняем новый фрейм данными из таблицы
# # def add_columns_rows(iter):
# #     '''
# #     Функция добавляет столбец при совпадении элементов в первой паре словаря и 
# #     добавляет строку при совпадении элементов второй пары словаря или 
# #     несовпадении в первой паре 
# #     '''
# #     global new_tab 
# #     if [dates_list[iter][0][0]] == list(data.values())[0] and [dates_list[iter][0][2]] == list(data.values())[1] \
# #     and [dates_list[iter][0][1]] == list(data.values())[2] and [dates_list[iter][0][3]] == list(data.values())[3]:
# #         if dates_list[iter][1] in new_tab.columns:
# #             new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
# #         else:
# #             new_tab.insert(loc=len(new_tab.columns), column=dates_list[iter][1], value='')
# #             new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
# #     else:
# #         data['Версия'] = [dates_list[iter][0][0]]
# #         data['Ядро'] = [dates_list[iter][0][2]]
# #         data['Режим'] = [dates_list[iter][0][1]]
# #         data['№ стенда'] = [dates_list[iter][0][3]]
# #         new_tab = new_tab._append(data, ignore_index=True)
# #         if dates_list[iter][1] in new_tab.columns:
# #             new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
# #         else:
# #             new_tab.insert(loc=len(new_tab.columns), column=dates_list[iter][1], value='')
# #             new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
# # [add_columns_rows(item) for item in range(0, len(dates_list))]

# # print(new_tab.T)


# # #Наводим красоту
# # new_tab.fillna('', inplace=True)
# # columns = ['Версия', 'Ядро', 'Режим', '№ стенда']
# # for col in columns:
# #     new_tab[col] = new_tab[col].astype(str).str.replace(r'\[|\]|\'', '', regex=True)



# # for k, v in testname_columns.items():
# #     new_tab.rename(columns={k:v}, inplace=True)
# # for name in new_tab.columns:
# #     new_tab[name] = where(new_tab[name] == 'NOT_EXECUTED', 'Не запускался', new_tab[name])
# #     new_tab[name] = where(new_tab[name] == 'IN_PROGRESS', 'Выполняется', new_tab[name])
# #     new_tab[name] = where(new_tab[name] == 'PASS', 'Выполнено', new_tab[name])
# #     new_tab[name] = where(new_tab[name] == 'FAIL', 'Провалено', new_tab[name])



# # new_tab = new_tab.sort_values(by=['Режим', '№ стенда'], ascending=[True, True])
# # new_tab = new_tab[[x for x in new_tab if x not in new_tab.columns[4:].sort_values()] 
# #                 + [x for x in new_tab.columns[4:].sort_values() if x in new_tab]]

# # new_tab = new_tab.T
# # new_tab.columns = pd.MultiIndex.from_tuples(zip(new_tab.columns, new_tab.iloc[0]))
# # new_tab = new_tab[1:]
# # print(new_tab)
# #test

# import psycopg2

# def add_value(value):
#     conn = psycopg2.connect(host='127.0.0.1',
#                             database='b_config',
#                             user='postgres',
#                             password='1'
#                             )

#     cursor = conn.cursor()
#     values = list(value)

#     if len(values) > 1:
#         for value in values:
#             insert_query = f"INSERT INTO main_table (release_version) VALUES ('{value}')"
#             cursor.execute(insert_query)
#     else:
#         insert_query = f"INSERT INTO main_table (release_version) VALUES ('{value}')"
#         cursor.execute(insert_query)

#     conn.commit()
#     cursor.close()
#     conn.close()


# #add_value('5')

# import json
# # from backup_image_conf import releases_dict, rc_list, releases_list, release_version, cycle_tree_index, releases, kernels
# # from backup_image_command import cz_comm

# def write_allta_conf(data):
#     # data = {'releases_dict':releases_dict,
#     #         'rc_list':rc_list,
#     #         'releases_list':releases_list,
#     #         'release_version':release_version,
#     #         'cycle_tree_index':cycle_tree_index,
#     #         'releases':releases,
#     #         'cz_comm':cz_comm,
#     #         'kernels':kernels}

#     with open('./allta_conf.json', 'w') as w:
#         json.dump(data, w, indent=4)



# #write_allta_conf()




# def mod_allta_conf(value):
#     with open('../allta_conf.json', 'r') as r:
#         data = json.load(r)

#     stands = {'LowServer':'10.177.103.204',
#               'MiddleServer':'10.177.103.203'}
#     cz_name = 'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "{}" \
#                 -l ru_RU.UTF-8 startdisk restore {}-{}rc{} nvme0n1'

#     if value not in data['releases_dict'].keys():
#         data['releases_dict'][value] = value
#     if value not in data['rc_list']:
#         data['rc_list'].append(value)
#     if '.'.join(value.split('.')[:3]) not in data['releases_list']:
#         data['releases_list'].append('.'.join(value.split('.')[:3]))
#     if value not in data['release_version']:
#         data['release_version'].append(value)
#     if value not in data['releases']:
#         data['releases'].append(value)        

#     if value not in data['cz_comm']['stand3'].keys():
#         data['cz_comm']['stand3'][value] = cz_name.format(stands['LowServer'],
#                                                           'LowServer',
#                                                           ''.join(value.split('.')[:3]),
#                                                           ''.join(value.split('.')[3:]))
#     if value not in data['cz_comm']['stand4'].keys():
#         data['cz_comm']['stand4'][value] = cz_name.format(stands['MiddleServer'],
#                                                           'MiddleServer',
#                                                           ''.join(value.split('.')[:3]),
#                                                           ''.join(value.split('.')[3:]))

#     write_allta_conf(data)



# #mod_allta_conf('1.8.1.3')

# #print(''.join('1.8.1.3'.split('.')[3:]))

# def update_changelog(value):
#     path = '../ChangeLog'
#     with open(path, 'r') as r:
#         version = r.readline()
#         text = r.read()
#     upp_version = int(version.split(' ')[2].split('.')[-1]) + 1
#     pre_version = '.'.join(version.split(' ')[2].split('.')[:-1])
#     new_version = f'{' '.join(version.split(' ')[:-1])} {pre_version}.{upp_version}'

#     print(version)
#     print(new_version)
#     print('.'.join(version.split(' ')[2].split('.')[:-1]))

#     commit = f'{new_version}\n* Add {value}\n\n\n\n\n'

#     with open(path, 'w') as w:
#         w.write(f'{commit}\n{version}{text}')

# #update_changelog('1.8.1.3')



# import requests





# #https://jira.astralinux.ru/rest/tests/1.0/testrun/search?fields=id,key,name,folderId,iterationId,projectVersionId,environmentId,userKeys,environmentIds,plannedStartDate,plannedEndDate,executionTime,estimatedTime,testResultStatuses,testCaseCount,issueCount,status(id,name,i18nKey,color),customFieldValues,createdOn,createdBy,updatedOn,updatedBy,owner&query=testRun.projectId IN (11200) AND testRun.folderTreeId IN (2744) ORDER BY testRun.name ASC&maxResults=40&startAt=0&archived=false
# #https://jira.astralinux.ru/rest/tests/1.0/testrun/search?fields=id,key,name,folderId,iterationId,projectVersionId,environmentId,userKeys,environmentIds,plannedStartDate,plannedEndDate,executionTime,estimatedTime,testResultStatuses,testCaseCount,issueCount,status(id,name,i18nKey,color),customFieldValues,createdOn,createdBy,updatedOn,updatedBy,owner&query=testRun.projectId IN (11200) AND testRun.folderTreeId IN (6579) ORDER BY testRun.name ASC&maxResults=40&startAt=0&archived=false

# #{"testCase":{"selectedFolderId":6350,"gridColumnSettings":{"6":{"index":14,"isVisible":false},"8":{"index":17,"isVisible":false},"10":{"index":15,"isVisible":false},"priority":{"index":0,"isVisible":true},"key":{"index":1,"isVisible":true},"majorVersion":{"index":2,"isVisible":true},"name":{"index":3,"isVisible":true},"status":{"index":4,"isVisible":true},"lastTestResultStatus":{"index":5,"isVisible":true},"owner":{"index":6,"isVisible":false},"estimatedTime":{"isVisible":true,"index":7},"component":{"index":8,"isVisible":false},"settings":{"index":9999,"isVisible":true},"labels":{"index":9,"isVisible":false},"createdOn":{"index":10,"isVisible":false},"createdBy":{"index":11,"isVisible":false},"updatedOn":{"index":12,"isVisible":false},"updatedBy":{"index":13,"isVisible":false}},"gridSortSettings":null},"testCycle":{"selectedFolderId":2744,"gridColumnSettings":null,"gridSortSettings":null,"groupingId":"list"},"testPlan":{"selectedFolderId":"root","gridColumnSettings":null,"gridSortSettings":null}}

# __basic = ''
# JIRA_URL = 'jira.astralinux.ru'

# def write_allta_conf(data):
#     with open('./allta_conf.json', 'w') as w:
#         json.dump(data, w, indent=4)



# def add_testrun_folder(rc):
#     main_folder = 2744
#     counter = 0

#     def __create_testrun_folder(name, parentid=main_folder):
#             add_folder_url = f'https://{JIRA_URL}/rest/tests/1.0/folder/testrun'
#             headers = {
#                 'Authorization': __basic
#             }
#             data = {
#                     "name": name,
#                     "projectId": 11200,
#                     "parentId": int(parentid)
#                     }

#             print(data)
#             response = requests.post(add_folder_url, headers=headers, json=data)
#             print(response.status_code)
#             print(response.text)
#             value = response.json()
#             config['cycle_tree_index'][name] = str(value['id'])
#             write_allta_conf(config)

#     while counter < 2:
#         counter += 1
#         with open('./allta_conf.json', 'r') as r:
#             config = json.load(r)

#         print(config['cycle_tree_index'].keys())
#         if rc not in config['cycle_tree_index'].keys():
#             check_len_version = rc.split('.')
#             if len(check_len_version) == 4 and check_len_version[3] != 'UU':
#                 if '.'.join(check_len_version[:3]) in config['cycle_tree_index'].keys():
#                     parentid = config['cycle_tree_index']['.'.join(check_len_version[:3])]
#                     name = rc
#                     __create_testrun_folder(name, parentid)
#                 else: __create_testrun_folder('.'.join(check_len_version[:3]))
#             elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
#                 if '.'.join(check_len_version[:5]) in config['cycle_tree_index'].keys():
#                     parentid = config['cycle_tree_index']['.'.join(check_len_version[:5])]
#                     name = rc
#                     __create_testrun_folder(name, parentid)
#                 else: __create_testrun_folder('.'.join(check_len_version[:5]))
#             else: 
#                 name = rc
#                 __create_testrun_folder(name)

        


# # #TODO добавить новые ядра в конфиг
# # from libs.liballta import get_kernels_from_rc

# # print(get_kernels_from_rc('1.7.6.5', get_list=True))

# # with open('./allta_conf.json', 'r') as r:
# #         config = json.load(r)

# # rc_kernels = get_kernels_from_rc('1.7.6.5', get_list=True)
# # print(rc_kernels)
# # config['kernels'] += [kern for kern in rc_kernels if kern not in set(config['kernels'])]

# # print(config['kernels'])




# def generate_repo_path():
#     pkg_path = '/dists/{}/main/binary-amd64/Packages'
#     vers_path = '/dists/{}/Release'

#     def sort_element(repo_list: list, element):
#         [repo_list.insert(0, repo_list.pop(repo_list.index(i))) for i in repo_list if element in i]
#         return repo_list[0]

#     with open('./releases.json', 'r') as rj:
#         links = json.load(rj)

#     pkg_path_dict = {
#         f"pkg_path_{key}": sort_element([f"{value.split(' ')[1]}{pkg_path}".format(value.split(' ')[2])  
#         for value in links[key] if any(x in value for x in ['devel-repository', 'base-repository', 'installation'])], 'installation')
#         for key in links.keys()
#     }

#     vers_path_dict = {
#         f"vers_path_{key}": sort_element([f"{value.split(' ')[1]}{vers_path}".format(value.split(' ')[2])   
#         for value in links[key] if any(x in value for x in ['devel-repository', 'base-repository', 'installation'])], 'installation')
#         for key in links.keys()
# }  

#     return {**pkg_path_dict, **vers_path_dict}


# #print(generate_repo_path())


# version = '1.8.1.3'
# release = '.'.join(version.split('.')[:3])
# #print(release)

# st = 'stand3'
# print(list(st)[-1])




# #response_check = requests.get(url=check_folder_url, headers=headers)
# #print(response_check.status_code)
# #print(response_check.text)


# # session = requests.Session()
# # session.headers.update({
# #     'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
# #     'authority': JIRA_URL,
# #     'Authorization': __basic,
# #     'X-Atlassian-Token': 'no-check',
# #     'accept': 'application/json, text/plain, */*'
# # })

# # response = session.get(check_folder_url)
# # print(response.status_code)
# # xsrf_token = session.cookies.get('atlassian.xsrf.token')
# # print(xsrf_token)

# # session.headers.update({
# #     'Cookie': f'atlassian.xsrf.token={xsrf_token}',
# #  })

# # response = session.post(add_folder_url, json=data)
# # print(session.cookies.get('atlassian.xsrf.token'))
# # print(response.status_code)
# # print(response.text)




# resp_check = {"projectId":11200,"itemsCount":5095,"children":[{"id":1145,"projectId":11200,"index":0,"name":"Ленинград 8.1","itemsCount":45,"children":[{"id":1979,"projectId":11200,"parentId":1145,"index":0,"name":"Update 3","itemsCount":10,"children":[],"createdBy":"dkalabanov","createdOn":"2021-08-09T08:19:09.099Z","updatedBy":"dkalabanov","updatedOn":"2021-09-30T00:00:00.000Z"},{"id":1403,"projectId":11200,"parentId":1145,"index":1,"name":"Update 2","itemsCount":2,"children":[],"createdBy":"dkalabanov","createdOn":"2020-05-13T09:17:35.452Z","updatedBy":"dkalabanov","updatedOn":"2021-09-30T00:00:00.000Z"},{"id":1402,"projectId":11200,"parentId":1145,"index":2,"name":"Update 1","itemsCount":6,"children":[],"createdBy":"dkalabanov","createdOn":"2020-05-13T09:17:18.140Z","updatedBy":"dkalabanov","updatedOn":"2021-09-30T00:00:00.000Z"},{"id":2602,"projectId":11200,"parentId":1145,"index":3,"name":"Update 4","itemsCount":24,"children":[],"createdBy":"JIRAUSER28813","createdOn":"2022-12-05T11:19:57.813Z"},{"id":5966,"projectId":11200,"parentId":1145,"index":4,"name":"Update 5","itemsCount":3,"children":[],"createdBy":"JIRAUSER28813","createdOn":"2024-02-13T08:45:49.560Z"}],"createdBy":"dkalabanov","createdOn":"2019-11-07T05:11:51.623Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":1100,"projectId":11200,"index":1,"name":"Минск 7.6","itemsCount":3,"children":[],"createdBy":"skruchkov","createdOn":"2019-09-18T15:23:49.044Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":3016,"projectId":11200,"index":2,"name":"БМ","itemsCount":239,"children":[],"createdBy":"abuynichenkov","createdOn":"2023-08-16T06:54:08.771Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":1187,"projectId":11200,"index":3,"name":"Новороссийск 4.1.1","itemsCount":9,"children":[],"createdBy":"dkalabanov","createdOn":"2020-03-24T13:54:26.844Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":11,"projectId":11200,"index":4,"name":"Орел 2.12","itemsCount":224,"children":[{"id":1400,"projectId":11200,"parentId":11,"index":0,"name":"2.12.24 +","itemsCount":7,"children":[],"createdBy":"dkalabanov","createdOn":"2020-04-13T07:52:12.474Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-03-03T00:00:00.000Z"},{"id":796,"projectId":11200,"parentId":11,"index":1,"name":"2.12.15 +","itemsCount":11,"children":[],"createdBy":"dkalabanov","createdOn":"2019-07-07T17:29:19.738Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-03-03T00:00:00.000Z"},{"id":495,"projectId":11200,"parentId":11,"index":2,"name":"2.12.14","itemsCount":3,"children":[],"updatedBy":"JIRAUSER34075","updatedOn":"2023-03-03T00:00:00.000Z"},{"id":1448,"projectId":11200,"parentId":11,"index":3,"name":"2.12.30+","itemsCount":24,"children":[],"createdBy":"skruchkov","createdOn":"2020-10-01T13:55:59.740Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-03-03T00:00:00.000Z"},{"id":1832,"projectId":11200,"parentId":11,"index":4,"name":"2.12.41+","itemsCount":14,"children":[],"createdBy":"dkalabanov","createdOn":"2021-01-22T12:14:31.209Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-03-03T00:00:00.000Z"},{"id":1905,"projectId":11200,"parentId":11,"index":5,"name":"2.12.43+","itemsCount":48,"children":[],"createdBy":"skruchkov","createdOn":"2021-05-17T14:33:08.354Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-03-03T00:00:00.000Z"},{"id":2155,"projectId":11200,"parentId":11,"index":6,"name":"2.12.44","itemsCount":4,"children":[],"createdBy":"dkalabanov","createdOn":"2022-01-28T09:10:09.048Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-03-03T00:00:00.000Z"},{"id":2263,"projectId":11200,"parentId":11,"index":7,"name":"2.12.45","itemsCount":50,"children":[],"createdBy":"abuynichenkov","createdOn":"2022-04-16T10:13:13.966Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-03-03T00:00:00.000Z"},{"id":2709,"projectId":11200,"parentId":11,"index":8,"name":"2.12.46","itemsCount":62,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-02-27T10:47:28.813Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-03-03T00:00:00.000Z"}],"updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":1158,"projectId":11200,"index":5,"name":"Орел 2.13","itemsCount":1,"children":[],"createdBy":"dkalabanov","createdOn":"2019-12-14T13:51:39.241Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":705,"projectId":11200,"index":6,"name":"Севастополь 6.2.1","itemsCount":1,"children":[],"createdBy":"dkalabanov","createdOn":"2019-06-17T08:36:33.320Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":9,"projectId":11200,"index":7,"name":"Смоленск 1.5","itemsCount":8,"children":[{"id":800,"projectId":11200,"parentId":9,"index":0,"name":"Обновление безопасности 22.08.2019","itemsCount":1,"children":[],"createdBy":"dkalabanov","createdOn":"2019-07-19T22:59:25.755Z"},{"id":1462,"projectId":11200,"parentId":9,"index":1,"name":"Update 9","itemsCount":7,"children":[],"createdBy":"dkalabanov","createdOn":"2020-12-01T15:28:06.078Z"}],"updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":10,"projectId":11200,"index":8,"name":"Смоленск 1.6","itemsCount":425,"children":[{"id":6409,"projectId":11200,"parentId":10,"index":0,"name":"Update 15","itemsCount":35,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2024-04-13T12:13:35.369Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":5704,"projectId":11200,"parentId":10,"index":1,"name":"Update 14","itemsCount":2,"children":[],"createdBy":"JIRAUSER28813","createdOn":"2023-12-26T15:29:35.918Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":4832,"projectId":11200,"parentId":10,"index":2,"name":"Update 13","itemsCount":66,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-11-15T13:35:47.925Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":2586,"projectId":11200,"parentId":10,"index":3,"name":"Update 12","itemsCount":70,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-11-30T09:26:30.297Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":2289,"projectId":11200,"parentId":10,"index":4,"name":"Update 11","itemsCount":40,"children":[],"createdBy":"abuynichenkov","createdOn":"2022-06-09T09:27:55.610Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":2048,"projectId":11200,"parentId":10,"index":5,"name":"Update 10","itemsCount":40,"children":[],"createdBy":"abuynichenkov","createdOn":"2021-11-18T15:23:50.953Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":2022,"projectId":11200,"parentId":10,"index":6,"name":"Update 9","itemsCount":34,"children":[],"createdBy":"skruchkov","createdOn":"2021-09-14T13:32:59.387Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":1974,"projectId":11200,"parentId":10,"index":7,"name":"Update 8","itemsCount":15,"children":[],"createdBy":"dkalabanov","createdOn":"2021-07-29T14:32:12.528Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":1886,"projectId":11200,"parentId":10,"index":8,"name":"Update 7","itemsCount":38,"children":[],"createdBy":"imanyakov","createdOn":"2021-04-22T13:57:02.852Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":1423,"projectId":11200,"parentId":10,"index":9,"name":"Update 6","itemsCount":12,"children":[],"createdBy":"dkalabanov","createdOn":"2020-06-08T06:47:33.867Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":1164,"projectId":11200,"parentId":10,"index":10,"name":"Update 5","itemsCount":11,"children":[],"createdBy":"skruchkov","createdOn":"2020-01-24T11:46:55.868Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":1138,"projectId":11200,"parentId":10,"index":11,"name":"Update 4","itemsCount":4,"children":[],"createdBy":"dkalabanov","createdOn":"2019-10-29T08:24:23.563Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":799,"projectId":11200,"parentId":10,"index":12,"name":"Update 3","itemsCount":16,"children":[],"createdBy":"skruchkov","createdOn":"2019-07-13T08:21:08.694Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":1108,"projectId":11200,"parentId":10,"index":13,"name":"Experimental","itemsCount":15,"children":[],"createdBy":"dkalabanov","createdOn":"2019-10-04T09:32:26.811Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":1130,"projectId":11200,"parentId":10,"index":14,"name":"Учебные","itemsCount":10,"children":[],"createdBy":"dkalabanov","createdOn":"2019-10-14T22:07:41.628Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":1432,"projectId":11200,"parentId":10,"index":15,"name":"ФСБ","itemsCount":3,"children":[],"createdBy":"dkalabanov","createdOn":"2020-07-28T11:40:34.869Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"},{"id":1450,"projectId":11200,"parentId":10,"index":16,"name":"Тех. диски","itemsCount":5,"children":[],"createdBy":"dkalabanov","createdOn":"2020-10-16T06:18:04.575Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-04-13T00:00:00.000Z"}],"updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":1458,"projectId":11200,"index":9,"name":"1.7","itemsCount":2255,"children":[{"id":6312,"projectId":11200,"parentId":1458,"index":0,"name":"1.7.6","itemsCount":218,"children":[{"id":6313,"projectId":11200,"parentId":6312,"index":0,"name":"Смоленск","itemsCount":77,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2024-03-21T20:42:31.688Z"},{"id":6314,"projectId":11200,"parentId":6312,"index":1,"name":"Воронеж","itemsCount":71,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2024-03-21T20:42:35.752Z"},{"id":6315,"projectId":11200,"parentId":6312,"index":2,"name":"Орел","itemsCount":70,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2024-03-21T20:42:41.956Z"}],"createdBy":"JIRAUSER34075","createdOn":"2024-03-21T20:42:17.715Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":5456,"projectId":11200,"parentId":1458,"index":1,"name":"1.7.5.UU1","itemsCount":407,"children":[{"id":5457,"projectId":11200,"parentId":5456,"index":0,"name":"Смоленск","itemsCount":181,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-11-24T10:54:53.481Z"},{"id":5458,"projectId":11200,"parentId":5456,"index":1,"name":"Воронеж","itemsCount":114,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-11-24T10:54:57.821Z"},{"id":5459,"projectId":11200,"parentId":5456,"index":2,"name":"Орел","itemsCount":111,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-11-24T10:55:02.517Z"}],"createdBy":"JIRAUSER34075","createdOn":"2023-11-24T10:54:44.745Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2983,"projectId":11200,"parentId":1458,"index":2,"name":"1.7.5","itemsCount":385,"children":[{"id":3021,"projectId":11200,"parentId":2983,"index":0,"name":"Смоленск","itemsCount":168,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-08-22T21:53:20.987Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-09-07T00:00:00.000Z"},{"id":3022,"projectId":11200,"parentId":2983,"index":1,"name":"Воронеж","itemsCount":108,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-08-22T21:53:25.260Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-09-07T00:00:00.000Z"},{"id":3023,"projectId":11200,"parentId":2983,"index":2,"name":"Орел","itemsCount":109,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-08-22T21:53:30.720Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-09-07T00:00:00.000Z"}],"createdBy":"JIRAUSER34075","createdOn":"2023-07-28T13:16:34.856Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2868,"projectId":11200,"parentId":1458,"index":3,"name":"1.7.4.UU1","itemsCount":229,"children":[{"id":2869,"projectId":11200,"parentId":2868,"index":0,"name":"Смоленск","itemsCount":97,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-06-02T12:29:38.053Z"},{"id":2870,"projectId":11200,"parentId":2868,"index":1,"name":"Орел","itemsCount":66,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-06-02T12:29:44.811Z","updatedBy":"JIRAUSER34075","updatedOn":"2023-06-02T00:00:00.000Z"},{"id":2871,"projectId":11200,"parentId":2868,"index":2,"name":"Воронеж","itemsCount":66,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-06-02T12:29:56.012Z"}],"createdBy":"JIRAUSER34075","createdOn":"2023-06-02T11:15:18.886Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2741,"projectId":11200,"parentId":1458,"index":4,"name":"1.7.4","itemsCount":216,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-03-20T04:58:23.008Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2695,"projectId":11200,"parentId":1458,"index":5,"name":"1.7.3.UU2","itemsCount":64,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-02-15T15:01:21.286Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2618,"projectId":11200,"parentId":1458,"index":6,"name":"1.7.3.UU1","itemsCount":93,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-12-12T18:15:59.971Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2483,"projectId":11200,"parentId":1458,"index":7,"name":"1.7.3","itemsCount":131,"children":[],"createdBy":"abuynichenkov","createdOn":"2022-10-08T05:41:38.053Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2441,"projectId":11200,"parentId":1458,"index":8,"name":"7.7.2(Минск)","itemsCount":37,"children":[],"createdBy":"abuynichenkov","createdOn":"2022-09-02T07:30:08.724Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2471,"projectId":11200,"parentId":1458,"index":9,"name":"1.7.2.UU1","itemsCount":52,"children":[],"createdBy":"abuynichenkov","createdOn":"2022-09-24T08:10:08.065Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2291,"projectId":11200,"parentId":1458,"index":10,"name":"1.7.2","itemsCount":111,"children":[],"createdBy":"JIRAUSER28813","createdOn":"2022-06-10T13:50:33.623Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2032,"projectId":11200,"parentId":1458,"index":11,"name":"1.7.1","itemsCount":81,"children":[],"createdBy":"dkalabanov","createdOn":"2021-10-19T08:14:15.990Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2164,"projectId":11200,"parentId":1458,"index":12,"name":"1.7.0","itemsCount":51,"children":[],"createdBy":"dkalabanov","createdOn":"2022-02-10T10:29:59.238Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2025,"projectId":11200,"parentId":1458,"index":13,"name":"Учебный","itemsCount":33,"children":[],"createdBy":"JIRAUSER28813","createdOn":"2021-09-30T08:32:56.001Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2035,"projectId":11200,"parentId":1458,"index":14,"name":"Experimental","itemsCount":6,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2021-11-08T14:43:52.975Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2417,"projectId":11200,"parentId":1458,"index":15,"name":"1.7.2.EXT1","itemsCount":19,"children":[],"createdBy":"abuynichenkov","createdOn":"2022-08-16T15:16:45.889Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2482,"projectId":11200,"parentId":1458,"index":16,"name":"1.7.2.EXT2","itemsCount":14,"children":[],"createdBy":"abuynichenkov","createdOn":"2022-10-06T04:01:27.117Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2525,"projectId":11200,"parentId":1458,"index":17,"name":"1.7.3.EXT1","itemsCount":30,"children":[],"createdBy":"abuynichenkov","createdOn":"2022-11-07T18:05:16.408Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":2791,"projectId":11200,"parentId":1458,"index":18,"name":"1.7.4.EXT1","itemsCount":32,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-05-11T08:25:44.095Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"},{"id":4770,"projectId":11200,"parentId":1458,"index":19,"name":"1.7.5.EXT1","itemsCount":46,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-10-24T13:41:28.149Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-17T00:00:00.000Z"}],"createdBy":"skruchkov","createdOn":"2020-11-08T09:58:31.774Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":4883,"projectId":11200,"index":10,"name":"1.8","itemsCount":243,"children":[{"id":4884,"projectId":11200,"parentId":4883,"index":0,"name":"1.8.0","itemsCount":207,"children":[],"createdBy":"JIRAUSER28813","createdOn":"2023-11-17T10:04:26.844Z"},{"id":6498,"projectId":11200,"parentId":4883,"index":1,"name":"1.8.1","itemsCount":34,"children":[{"id":6499,"projectId":11200,"parentId":6498,"index":0,"name":"Смоленск","itemsCount":12,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2024-04-28T07:03:46.033Z"},{"id":6500,"projectId":11200,"parentId":6498,"index":1,"name":"Воронеж","itemsCount":10,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2024-04-28T07:03:53.952Z"},{"id":6501,"projectId":11200,"parentId":6498,"index":2,"name":"Орел","itemsCount":12,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2024-04-28T07:03:59.265Z","updatedBy":"JIRAUSER34075","updatedOn":"2024-05-13T00:00:00.000Z"}],"createdBy":"JIRAUSER34075","createdOn":"2024-04-28T07:03:35.531Z"}],"createdBy":"JIRAUSER28813","createdOn":"2023-11-17T10:04:13.922Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":1869,"projectId":11200,"index":11,"name":"Новороссийск 4.7","itemsCount":144,"children":[{"id":2034,"projectId":11200,"parentId":1869,"index":0,"name":"4.7.1","itemsCount":17,"children":[],"createdBy":"skruchkov","createdOn":"2021-11-02T08:44:02.162Z","updatedBy":"JIRAUSER34075","updatedOn":"2022-10-13T00:00:00.000Z"},{"id":2047,"projectId":11200,"parentId":1869,"index":1,"name":"4.7.0","itemsCount":12,"children":[],"createdBy":"dkalabanov","createdOn":"2021-11-17T11:32:42.828Z","updatedBy":"JIRAUSER34075","updatedOn":"2022-10-13T00:00:00.000Z"},{"id":2295,"projectId":11200,"parentId":1869,"index":2,"name":"4.7.2","itemsCount":21,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-06-20T14:49:19.092Z","updatedBy":"JIRAUSER34075","updatedOn":"2022-10-13T00:00:00.000Z"},{"id":2458,"projectId":11200,"parentId":1869,"index":3,"name":"extended","itemsCount":17,"children":[],"createdBy":"JIRAUSER30795","createdOn":"2022-09-09T06:38:33.736Z","updatedBy":"JIRAUSER34075","updatedOn":"2022-10-13T00:00:00.000Z"},{"id":2489,"projectId":11200,"parentId":1869,"index":4,"name":"4.7.3","itemsCount":13,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-10-13T16:28:24.739Z","updatedBy":"JIRAUSER34075","updatedOn":"2022-10-13T00:00:00.000Z"},{"id":2636,"projectId":11200,"parentId":1869,"index":5,"name":"4.7.3.UU1","itemsCount":13,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-12-22T14:39:40.328Z"},{"id":2739,"projectId":11200,"parentId":1869,"index":6,"name":"4.7.3.UU2","itemsCount":6,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-03-16T18:48:30.402Z"},{"id":2784,"projectId":11200,"parentId":1869,"index":7,"name":"4.7.4","itemsCount":17,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-05-02T09:10:40.113Z"},{"id":2995,"projectId":11200,"parentId":1869,"index":8,"name":"4.7.4.UU1","itemsCount":4,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-08-04T09:21:35.890Z"},{"id":3042,"projectId":11200,"parentId":1869,"index":9,"name":"4.7.4.UU1.2","itemsCount":5,"children":[],"createdBy":"JIRAUSER30656","createdOn":"2023-08-29T17:26:36.628Z"},{"id":4783,"projectId":11200,"parentId":1869,"index":10,"name":"4.7.5","itemsCount":19,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2023-10-30T10:11:11.897Z"}],"createdBy":"skruchkov","createdOn":"2021-04-14T14:15:43.948Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":1834,"projectId":11200,"index":12,"name":"Брест","itemsCount":897,"children":[{"id":2046,"projectId":11200,"parentId":1834,"index":0,"name":"Учебные","itemsCount":1,"children":[],"createdBy":"slondyrev","createdOn":"2021-11-16T09:28:13.885Z","updatedBy":"JIRAUSER49695","updatedOn":"2023-08-30T00:00:00.000Z"},{"id":2598,"projectId":11200,"parentId":1834,"index":1,"name":"2.х","itemsCount":165,"children":[{"id":2049,"projectId":11200,"parentId":2598,"index":0,"name":"2.6","itemsCount":28,"children":[],"createdBy":"slondyrev","createdOn":"2021-11-19T07:28:59.691Z","updatedBy":"slondyrev","updatedOn":"2022-12-05T00:00:00.000Z"},{"id":2045,"projectId":11200,"parentId":2598,"index":1,"name":"2.7_2.9","itemsCount":82,"children":[],"createdBy":"slondyrev","createdOn":"2021-11-16T09:28:03.757Z","updatedBy":"slondyrev","updatedOn":"2022-12-05T00:00:00.000Z"},{"id":2600,"projectId":11200,"parentId":2598,"index":2,"name":"2.12","itemsCount":55,"children":[{"id":6323,"projectId":11200,"parentId":2600,"index":0,"name":"TESTO","itemsCount":22,"children":[],"createdBy":"slondyrev","createdOn":"2024-03-22T13:09:54.832Z"},{"id":6324,"projectId":11200,"parentId":2600,"index":1,"name":"manual_testing","itemsCount":33,"children":[],"createdBy":"slondyrev","createdOn":"2024-03-22T13:10:05.310Z"}],"createdBy":"slondyrev","createdOn":"2022-12-05T07:56:24.740Z"}],"createdBy":"slondyrev","createdOn":"2022-12-05T07:55:47.316Z","updatedBy":"JIRAUSER49695","updatedOn":"2023-08-30T00:00:00.000Z"},{"id":2599,"projectId":11200,"parentId":1834,"index":2,"name":"3.x","itemsCount":731,"children":[{"id":2083,"projectId":11200,"parentId":2599,"index":0,"name":"3.0-3.1","itemsCount":72,"children":[{"id":2084,"projectId":11200,"parentId":2083,"index":0,"name":"x86-64","itemsCount":69,"children":[],"createdBy":"slondyrev","createdOn":"2022-01-10T14:56:02.559Z"},{"id":2085,"projectId":11200,"parentId":2083,"index":1,"name":"arm64","itemsCount":3,"children":[],"createdBy":"slondyrev","createdOn":"2022-01-10T14:56:18.791Z"}],"createdBy":"slondyrev","createdOn":"2022-01-10T14:55:21.985Z","updatedBy":"slondyrev","updatedOn":"2024-03-25T00:00:00.000Z"},{"id":2459,"projectId":11200,"parentId":2599,"index":1,"name":"3.2","itemsCount":52,"children":[{"id":2460,"projectId":11200,"parentId":2459,"index":0,"name":"x86-64","itemsCount":52,"children":[],"createdBy":"slondyrev","createdOn":"2022-09-14T13:45:40.264Z"}],"createdBy":"slondyrev","createdOn":"2022-09-14T13:45:26.515Z","updatedBy":"slondyrev","updatedOn":"2024-03-25T00:00:00.000Z"},{"id":2772,"projectId":11200,"parentId":2599,"index":2,"name":"3.3","itemsCount":99,"children":[{"id":2776,"projectId":11200,"parentId":2772,"index":0,"name":"manual_testing","itemsCount":57,"children":[{"id":2938,"projectId":11200,"parentId":2776,"index":0,"name":"RC1","itemsCount":15,"children":[],"createdBy":"slondyrev","createdOn":"2023-06-21T13:32:57.978Z"},{"id":2939,"projectId":11200,"parentId":2776,"index":1,"name":"RC2","itemsCount":14,"children":[],"createdBy":"slondyrev","createdOn":"2023-06-21T13:33:35.705Z"},{"id":2940,"projectId":11200,"parentId":2776,"index":2,"name":"RC3","itemsCount":1,"children":[],"createdBy":"slondyrev","createdOn":"2023-06-21T13:34:24.639Z"},{"id":2969,"projectId":11200,"parentId":2776,"index":3,"name":"RC4","itemsCount":7,"children":[],"createdBy":"JIRAUSER37233","createdOn":"2023-07-14T09:54:03.131Z"},{"id":2970,"projectId":11200,"parentId":2776,"index":4,"name":"RC5","itemsCount":20,"children":[],"createdBy":"JIRAUSER37233","createdOn":"2023-07-14T09:54:09.001Z"}],"createdBy":"slondyrev","createdOn":"2023-04-20T12:30:02.250Z"},{"id":2777,"projectId":11200,"parentId":2772,"index":1,"name":"TESTO","itemsCount":42,"children":[{"id":2972,"projectId":11200,"parentId":2777,"index":0,"name":"RC5","itemsCount":14,"children":[],"createdBy":"JIRAUSER37233","createdOn":"2023-07-14T10:51:54.145Z","updatedBy":"JIRAUSER37233","updatedOn":"2023-07-14T00:00:00.000Z"},{"id":2971,"projectId":11200,"parentId":2777,"index":1,"name":"RC4","itemsCount":10,"children":[],"createdBy":"JIRAUSER37233","createdOn":"2023-07-14T10:51:34.698Z","updatedBy":"JIRAUSER37233","updatedOn":"2023-07-14T00:00:00.000Z"},{"id":2942,"projectId":11200,"parentId":2777,"index":2,"name":"RC3","itemsCount":0,"children":[],"createdBy":"slondyrev","createdOn":"2023-06-21T13:41:01.343Z","updatedBy":"JIRAUSER37233","updatedOn":"2023-07-14T00:00:00.000Z"},{"id":2941,"projectId":11200,"parentId":2777,"index":3,"name":"RC2","itemsCount":10,"children":[],"createdBy":"slondyrev","createdOn":"2023-06-21T13:40:54.654Z","updatedBy":"JIRAUSER37233","updatedOn":"2023-07-14T00:00:00.000Z"},{"id":2943,"projectId":11200,"parentId":2777,"index":4,"name":"RC1","itemsCount":8,"children":[],"createdBy":"slondyrev","createdOn":"2023-06-21T13:41:27.807Z","updatedBy":"JIRAUSER37233","updatedOn":"2023-07-14T00:00:00.000Z"}],"createdBy":"slondyrev","createdOn":"2023-04-20T12:30:12.946Z"}],"createdBy":"slondyrev","createdOn":"2023-04-19T12:41:56.318Z","updatedBy":"slondyrev","updatedOn":"2024-03-25T00:00:00.000Z"},{"id":5546,"projectId":11200,"parentId":2599,"index":3,"name":"3.3.1(1.7.4UU1)","itemsCount":350,"children":[{"id":5547,"projectId":11200,"parentId":5546,"index":0,"name":"TESTO","itemsCount":166,"children":[{"id":5556,"projectId":11200,"parentId":5547,"index":0,"name":"RC1","itemsCount":30,"children":[],"createdBy":"slondyrev","createdOn":"2023-12-11T13:45:58.909Z"},{"id":5872,"projectId":11200,"parentId":5547,"index":1,"name":"RC2","itemsCount":34,"children":[],"createdBy":"slondyrev","createdOn":"2024-01-25T07:58:48.173Z"},{"id":5960,"projectId":11200,"parentId":5547,"index":2,"name":"RC3","itemsCount":34,"children":[],"createdBy":"slondyrev","createdOn":"2024-02-13T07:55:56.663Z"},{"id":6172,"projectId":11200,"parentId":5547,"index":3,"name":"RC4","itemsCount":34,"children":[],"createdBy":"slondyrev","createdOn":"2024-03-05T08:49:28.108Z"},{"id":6243,"projectId":11200,"parentId":5547,"index":4,"name":"RC5","itemsCount":34,"children":[],"createdBy":"slondyrev","createdOn":"2024-03-11T07:31:25.106Z"}],"createdBy":"JIRAUSER37233","createdOn":"2023-12-07T09:43:17.509Z"},{"id":5548,"projectId":11200,"parentId":5546,"index":1,"name":"manual_testing","itemsCount":177,"children":[{"id":5557,"projectId":11200,"parentId":5548,"index":0,"name":"RC1","itemsCount":40,"children":[],"createdBy":"slondyrev","createdOn":"2023-12-11T13:48:01.937Z"},{"id":5870,"projectId":11200,"parentId":5548,"index":1,"name":"RC2","itemsCount":39,"children":[],"createdBy":"slondyrev","createdOn":"2024-01-24T15:53:16.922Z"},{"id":5959,"projectId":11200,"parentId":5548,"index":2,"name":"RC3","itemsCount":34,"children":[],"createdBy":"slondyrev","createdOn":"2024-02-13T07:55:48.490Z"},{"id":6171,"projectId":11200,"parentId":5548,"index":3,"name":"RC4","itemsCount":17,"children":[],"createdBy":"slondyrev","createdOn":"2024-03-05T08:49:20.891Z"},{"id":6244,"projectId":11200,"parentId":5548,"index":4,"name":"RC5","itemsCount":47,"children":[],"createdBy":"slondyrev","createdOn":"2024-03-11T07:31:37.795Z"}],"createdBy":"JIRAUSER37233","createdOn":"2023-12-07T09:43:29.963Z"},{"id":5812,"projectId":11200,"parentId":5546,"index":2,"name":"STRESS","itemsCount":7,"children":[{"id":5813,"projectId":11200,"parentId":5812,"index":0,"name":"RC1","itemsCount":0,"children":[],"createdBy":"slondyrev","createdOn":"2024-01-12T07:34:49.271Z"},{"id":5880,"projectId":11200,"parentId":5812,"index":1,"name":"RC2","itemsCount":2,"children":[],"createdBy":"slondyrev","createdOn":"2024-01-26T08:38:01.028Z"},{"id":5973,"projectId":11200,"parentId":5812,"index":2,"name":"RC3","itemsCount":2,"children":[],"createdBy":"slondyrev","createdOn":"2024-02-14T15:10:08.963Z"},{"id":6170,"projectId":11200,"parentId":5812,"index":3,"name":"RC4","itemsCount":2,"children":[],"createdBy":"slondyrev","createdOn":"2024-03-05T08:48:59.238Z"},{"id":6245,"projectId":11200,"parentId":5812,"index":4,"name":"RC5","itemsCount":1,"children":[],"createdBy":"slondyrev","createdOn":"2024-03-11T07:31:45.218Z"}],"createdBy":"slondyrev","createdOn":"2024-01-12T07:34:38.549Z"}],"createdBy":"JIRAUSER37233","createdOn":"2023-12-07T09:42:54.337Z","updatedBy":"slondyrev","updatedOn":"2024-03-25T00:00:00.000Z"},{"id":6326,"projectId":11200,"parentId":2599,"index":4,"name":"3.3.1(1.7.5UU1)","itemsCount":72,"children":[{"id":6327,"projectId":11200,"parentId":6326,"index":0,"name":"TESTO","itemsCount":35,"children":[],"createdBy":"slondyrev","createdOn":"2024-03-25T10:01:52.923Z"},{"id":6328,"projectId":11200,"parentId":6326,"index":1,"name":"manual_testing","itemsCount":37,"children":[],"createdBy":"slondyrev","createdOn":"2024-03-25T10:02:03.617Z"}],"createdBy":"slondyrev","createdOn":"2024-03-25T10:01:35.125Z","updatedBy":"slondyrev","updatedOn":"2024-03-25T00:00:00.000Z"},{"id":6319,"projectId":11200,"parentId":2599,"index":5,"name":"3.3.2(1.7.4UU1)","itemsCount":86,"children":[{"id":6320,"projectId":11200,"parentId":6319,"index":0,"name":"TESTO","itemsCount":39,"children":[{"id":6399,"projectId":11200,"parentId":6320,"index":0,"name":"RC1","itemsCount":39,"children":[],"createdBy":"slondyrev","createdOn":"2024-04-11T10:52:49.616Z"}],"createdBy":"slondyrev","createdOn":"2024-03-22T12:35:21.755Z"},{"id":6321,"projectId":11200,"parentId":6319,"index":1,"name":"MANUAL","itemsCount":47,"children":[{"id":6400,"projectId":11200,"parentId":6321,"index":0,"name":"RC1","itemsCount":47,"children":[{"id":6666,"projectId":11200,"parentId":6400,"index":0,"name":"Update","itemsCount":28,"children":[{"id":6669,"projectId":11200,"parentId":6666,"index":0,"name":"3.2","itemsCount":9,"children":[],"createdBy":"slondyrev","createdOn":"2024-05-24T15:16:06.905Z"},{"id":6670,"projectId":11200,"parentId":6666,"index":1,"name":"3.3","itemsCount":9,"children":[],"createdBy":"slondyrev","createdOn":"2024-05-24T15:16:13.667Z"},{"id":6671,"projectId":11200,"parentId":6666,"index":2,"name":"3.3.1","itemsCount":10,"children":[],"createdBy":"slondyrev","createdOn":"2024-05-24T15:16:21.380Z"}],"createdBy":"JIRAUSER37233","createdOn":"2024-05-24T09:17:14.071Z","updatedBy":"JIRAUSER37233","updatedOn":"2024-05-24T00:00:00.000Z"}],"createdBy":"slondyrev","createdOn":"2024-04-11T10:52:57.062Z"}],"createdBy":"slondyrev","createdOn":"2024-03-22T12:35:28.379Z","updatedBy":"JIRAUSER37233","updatedOn":"2024-05-23T00:00:00.000Z"},{"id":6322,"projectId":11200,"parentId":6319,"index":2,"name":"STRESS","itemsCount":0,"children":[],"createdBy":"slondyrev","createdOn":"2024-03-22T12:35:34.436Z"}],"createdBy":"slondyrev","createdOn":"2024-03-22T12:35:08.946Z","updatedBy":"slondyrev","updatedOn":"2024-05-23T00:00:00.000Z"}],"createdBy":"slondyrev","createdOn":"2022-12-05T07:55:56.836Z","updatedBy":"JIRAUSER49695","updatedOn":"2023-08-30T00:00:00.000Z"},{"id":3043,"projectId":11200,"parentId":1834,"index":3,"name":"Autocreated","itemsCount":0,"children":[{"id":3044,"projectId":11200,"parentId":3043,"index":0,"name":"TESTO","itemsCount":0,"children":[],"createdBy":"JIRAUSER49695","createdOn":"2023-08-31T08:47:11.593Z","updatedBy":"JIRAUSER43827","updatedOn":"2024-05-14T12:39:58.874Z"},{"id":3045,"projectId":11200,"parentId":3043,"index":1,"name":"MANUAL","itemsCount":0,"children":[],"createdBy":"JIRAUSER49695","createdOn":"2023-08-31T08:47:29.393Z","updatedBy":"JIRAUSER37233","updatedOn":"2024-05-23T00:00:00.000Z"}],"createdBy":"JIRAUSER49695","createdOn":"2023-08-31T08:47:02.652Z"}],"createdBy":"dkalabanov","createdOn":"2021-02-11T11:57:27.335Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":2862,"projectId":11200,"index":13,"name":"14.7_armhf","itemsCount":5,"children":[],"createdBy":"JIRAUSER28813","createdOn":"2023-05-30T14:31:35.637Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":1898,"projectId":11200,"index":14,"name":"КРЭА","itemsCount":77,"children":[],"createdBy":"dkalabanov","createdOn":"2021-04-30T08:02:58.081Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":2051,"projectId":11200,"index":15,"name":"tests","itemsCount":109,"children":[],"createdBy":"abuynichenkov","createdOn":"2021-11-30T06:15:28.981Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":2052,"projectId":11200,"index":16,"name":"CSP","itemsCount":82,"children":[{"id":2053,"projectId":11200,"parentId":2052,"index":0,"name":"5r3","itemsCount":16,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2021-11-30T07:59:02.759Z"},{"id":2261,"projectId":11200,"parentId":2052,"index":1,"name":"5r2 Kraken SP1","itemsCount":24,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-04-15T10:03:04.747Z"},{"id":2264,"projectId":11200,"parentId":2052,"index":2,"name":"Работа в ЗПС","itemsCount":7,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-04-20T11:49:27.486Z"},{"id":2412,"projectId":11200,"parentId":2052,"index":3,"name":"Q3 2022","itemsCount":6,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-08-10T07:13:58.080Z"},{"id":2614,"projectId":11200,"parentId":2052,"index":4,"name":"Q4 2022","itemsCount":29,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-12-08T10:06:18.430Z"}],"createdBy":"JIRAUSER34075","createdOn":"2021-11-30T07:58:55.619Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":2154,"projectId":11200,"index":17,"name":"policykit","itemsCount":14,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-01-27T13:28:30.479Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":2296,"projectId":11200,"index":18,"name":"Reruns","itemsCount":46,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-06-24T10:05:14.586Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":2530,"projectId":11200,"index":19,"name":"Autocreated","itemsCount":0,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2022-11-09T14:29:17.487Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":2664,"projectId":11200,"index":20,"name":"Manual","itemsCount":12,"children":[],"createdBy":"JIRAUSER48786","createdOn":"2023-01-28T09:51:44.670Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":2744,"projectId":11200,"index":21,"name":"stress_test","itemsCount":204,"children":[{"id":2745,"projectId":11200,"parentId":2744,"index":0,"name":"1.7.3.UU.1","itemsCount":12,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-03-27T15:28:48.887Z"},{"id":2773,"projectId":11200,"parentId":2744,"index":1,"name":"1.7.4","itemsCount":12,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-04-19T16:25:05.831Z"},{"id":2774,"projectId":11200,"parentId":2744,"index":2,"name":"1.7.3","itemsCount":12,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-04-20T08:46:22.366Z"},{"id":2808,"projectId":11200,"parentId":2744,"index":3,"name":"1.7.3.UU.2","itemsCount":12,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-05-18T17:51:58.383Z"},{"id":2936,"projectId":11200,"parentId":2744,"index":4,"name":"1.7.4.UU.1","itemsCount":12,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-06-20T11:25:22.887Z"},{"id":2937,"projectId":11200,"parentId":2744,"index":5,"name":"1.7.2","itemsCount":8,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-06-21T08:03:10.166Z"},{"id":2947,"projectId":11200,"parentId":2744,"index":6,"name":"1.7.1","itemsCount":4,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-06-22T08:03:09.918Z"},{"id":2967,"projectId":11200,"parentId":2744,"index":7,"name":"debian10","itemsCount":1,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-07-12T14:42:51.211Z","updatedBy":"JIRAUSER38882","updatedOn":"2023-07-12T00:00:00.000Z"},{"id":2974,"projectId":11200,"parentId":2744,"index":8,"name":"debian10-5.15","itemsCount":1,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-07-17T17:21:30.579Z"},{"id":2977,"projectId":11200,"parentId":2744,"index":9,"name":"debian11-6.1","itemsCount":1,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-07-19T10:50:59.372Z","updatedBy":"JIRAUSER38882","updatedOn":"2023-07-20T00:00:00.000Z"},{"id":2982,"projectId":11200,"parentId":2744,"index":10,"name":"altlinux-5.10","itemsCount":1,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-07-25T13:13:01.192Z","updatedBy":"JIRAUSER38882","updatedOn":"2023-07-25T00:00:00.000Z"},{"id":3032,"projectId":11200,"parentId":2744,"index":11,"name":"1.7.5","itemsCount":12,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-08-23T17:04:39.195Z"},{"id":5443,"projectId":11200,"parentId":2744,"index":12,"name":"1.8.0","itemsCount":34,"children":[{"id":5444,"projectId":11200,"parentId":5443,"index":0,"name":"1.8.0.5","itemsCount":4,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-11-22T09:27:04.867Z","updatedBy":"JIRAUSER38882","updatedOn":"2024-01-17T00:00:00.000Z"},{"id":5887,"projectId":11200,"parentId":5443,"index":1,"name":"1.8.0.6","itemsCount":4,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-01-30T10:30:33.820Z"},{"id":6044,"projectId":11200,"parentId":5443,"index":2,"name":"1.8.0.9","itemsCount":4,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-02-19T17:11:25.849Z"},{"id":6160,"projectId":11200,"parentId":5443,"index":3,"name":"1.8.0.10","itemsCount":4,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-02-28T14:52:36.790Z"},{"id":6256,"projectId":11200,"parentId":5443,"index":4,"name":"1.8.0.11","itemsCount":4,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-03-11T12:42:47.560Z"},{"id":6289,"projectId":11200,"parentId":5443,"index":5,"name":"1.8.0.12","itemsCount":4,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-03-15T16:52:38.539Z","updatedBy":"JIRAUSER38882","updatedOn":"2024-03-15T00:00:00.000Z"},{"id":6329,"projectId":11200,"parentId":5443,"index":6,"name":"1.8.0.13","itemsCount":4,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-03-25T10:44:13.575Z"},{"id":6332,"projectId":11200,"parentId":5443,"index":7,"name":"1.8.0.14","itemsCount":4,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-03-27T09:03:55.959Z"},{"id":6485,"projectId":11200,"parentId":5443,"index":8,"name":"1.8.0.15","itemsCount":2,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-04-24T17:44:49.217Z"}],"createdBy":"JIRAUSER38882","createdOn":"2023-11-22T09:26:54.087Z"},{"id":5538,"projectId":11200,"parentId":2744,"index":13,"name":"1.7.5.UU.1","itemsCount":28,"children":[{"id":5539,"projectId":11200,"parentId":5538,"index":0,"name":"1.7.5.UU.1.1","itemsCount":16,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2023-12-05T09:44:06.550Z"},{"id":5970,"projectId":11200,"parentId":5538,"index":1,"name":"1.7.5.UU.1.7","itemsCount":12,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-02-13T16:09:10.775Z"}],"createdBy":"JIRAUSER38882","createdOn":"2023-12-05T09:43:20.083Z","updatedBy":"JIRAUSER38882","updatedOn":"2023-12-05T00:00:00.000Z"},{"id":6345,"projectId":11200,"parentId":2744,"index":14,"name":"1.7.6","itemsCount":34,"children":[{"id":6346,"projectId":11200,"parentId":6345,"index":0,"name":"1.7.6.1","itemsCount":16,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-04-02T08:39:55.665Z"},{"id":6401,"projectId":11200,"parentId":6345,"index":1,"name":"1.7.6.3","itemsCount":16,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-04-11T13:35:49.897Z"},{"id":6467,"projectId":11200,"parentId":6345,"index":2,"name":"1.7.6.4","itemsCount":2,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-04-23T14:54:21.873Z"}],"createdBy":"JIRAUSER38882","createdOn":"2024-04-02T08:39:47.086Z"},{"id":6495,"projectId":11200,"parentId":2744,"index":15,"name":"1.8.1","itemsCount":20,"children":[{"id":6496,"projectId":11200,"parentId":6495,"index":0,"name":"1.8.1.1","itemsCount":8,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-04-27T08:40:35.551Z"},{"id":6531,"projectId":11200,"parentId":6495,"index":1,"name":"1.8.1.01","itemsCount":4,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-05-13T14:36:53.145Z"},{"id":6579,"projectId":11200,"parentId":6495,"index":2,"name":"1.8.1.2","itemsCount":8,"children":[],"createdBy":"JIRAUSER38882","createdOn":"2024-05-21T09:01:42.368Z"}],"createdBy":"JIRAUSER38882","createdOn":"2024-04-27T08:40:18.602Z"}],"createdBy":"JIRAUSER38882","createdOn":"2023-03-27T13:50:25.067Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":2783,"projectId":11200,"index":22,"name":"astra-mobile","itemsCount":44,"children":[],"createdBy":"JIRAUSER38965","createdOn":"2023-04-28T10:10:18.357Z","updatedBy":"JIRAUSER28813","updatedOn":"2023-11-17T00:00:00.000Z"},{"id":5899,"projectId":11200,"index":23,"name":"permanent","itemsCount":4,"children":[],"createdBy":"JIRAUSER34075","createdOn":"2024-02-02T16:10:53.252Z"}]}

# #print(len(resp_check['children'][21]['children'][-1]['children']))





# "Время выполнения прогона. Актуально на 27.11.2024"
# "TestTimeWatchdog"

# import pandas as pd
# import re
# import json
# from datetime import datetime, timedelta



# def tests_time_wrapper(file_path):
#     with open(file_path, 'r') as r:
#         testsrun = r.readlines()

#     for i in testsrun:
#         if 'Выбран релиз:' in i:
#             print(i.strip().split(' ')[-2])

#     testsdict = {}
#     current_test = None

#     for i in testsrun:
#         if 'Тест:' in i:
#             current_test = re.sub(r'\x1b\[\d+m', '', i.split('Тест: ')[1]).strip()
        
#         if 'Затрачено времени:' in i and current_test:
#             time_spent = i.split("Затрачено времени: ")[1].strip()
#             testsdict[current_test] = time_spent
#             current_test = None  

#     return testsdict



# alltime = {
#     "1.7": {
#         'stand3': tests_time_wrapper('front_stand3.log'),
#         'stand4': tests_time_wrapper('front_stand4.log')
#         }
# }
# print(alltime)


#with open('testjson.json', 'w') as w:
#    w.write(json.dumps(alltime, indent=4))

# class TestTimeWatchdog:
#     """
#     Класс позволяет вести динамический подсчет времени, 
#     затраченного на прогон с одним ядром.

#     :param str upd_version: upd version (1.7 or 1.8 etc)
#     :param str stand: номер стенда
#     """
#     def __init__(self,
#                  upd_version,
#                  stand):

#         self.upd_version = upd_version
#         self.stand = stand
#         self.times_path = 'test_times.json'


#     def transfer_test_time(self, test_name: str, time: str):
#         """
#         :param test_name: наименование теста в прогоне
#         :param time: время, затраченное на выполнения теста
#         """
#         with open(self.times_path, 'r') as r:
#             data = json.loads(r.read())
        
#         if self.upd_version in data and self.stand in data[self.upd_version]: 
#             data[self.upd_version][self.stand][test_name] = time

#         with open(self.times_path, 'w') as w:
#             json.dump(data, w, indent=4)


#     def _counting_total_time(self):
#         """
#         Подсчет общего времени, затраченного на прогон с одним ядром
#         """
#         with open(self.times_path, 'r') as r:
#             data = json.loads(r.read())

#         def parse_time(time_str):
#             return datetime.strptime(time_str, "%H:%M:%S")

#         def sum_times(times):
#             total = timedelta()
#             for time_str in times:
#                 total += timedelta(hours=parse_time(time_str).hour,
#                                 minutes=parse_time(time_str).minute,
#                                 seconds=parse_time(time_str).second)
#             return total

#         def format_time(total):
#             days = total.days
#             hours, remainder = divmod(total.seconds, 3600)
#             minutes, seconds = divmod(remainder, 60)
#             if days > 0:
#                 return f"{days} day {hours}:{minutes:02}:{seconds:02}"
#             else:
#                 return f"{hours}:{minutes:02}:{seconds:02}"

#         for version, stands in data.items():
#             for stand, tests in stands.items():
#                 times = [time for test, time in tests.items() if test != "Total time"]
#                 total_time = sum_times(times)
#                 data[version][stand]["Total time"] = format_time(total_time)

#         rows = {}
#         for version, stands in data.items():
#             for stand, tests in stands.items():
#                 for test, time in tests.items():
#                     if test not in rows:
#                         rows[test] = {}
#                     rows[test][(version, stand)] = time

#         return rows


#     def create_html(self):
#         """
#         Создает html на основе полученных данных
#         """

#         current_day = datetime.now().date()
#         html_head = f"""
#         <br />
#         <br />
#         <h1>Время выполнения прогона для одного ядра. Актуально на {current_day}</h1>
#         """

#         df = pd.DataFrame(self._counting_total_time()).transpose()
#         df = df.reindex(columns=sorted(df.columns, key=lambda x: (x[0], x[1])))

#         total_time_row = df.loc['Total time']
#         df = df.drop('Total time')
#         df = pd.concat([df, total_time_row.to_frame().T])
#         df.fillna('', inplace=True)

#         html_result = df.to_html()
#         html_page = '\n'.join([html_head + html_result])

#         print(html_page)
#         with open('testhtml.html', 'w') as w:
#             w.write(html_page)


# from libs.liballta import TestTimeWatchdog



# tt_watchdog = TestTimeWatchdog(upd_version='1.8',
#                                stand='stand4')

# tt_watchdog.transfer_test_time(test_name='FIO',
#                                time='4:41:20')
# tt_watchdog.create_html()


from libs.libconfluence import ConfluenceAPI


conf = ConfluenceAPI(username='dtimonin', token='MjEyOTcxNzIwOTY3OrXcjTH2BpyzcE8+4avH2EkN8U29')

#status_page = conf.get_full_page_by_id(page_id='347765252')
#html_content = status_page.get('body', {}).get('storage', {}).get('value', '')

#with open('status.html', 'w') as w:
#    w.write(html_content)
#span.css-14v6hkl:nth-child(15)
#css-14v6hkl e16vi2nm1
#print(status_page)



import requests
from bs4 import BeautifulSoup

__basic = ''

url = 'https://jira.astralinux.ru/rest/api/2/user/properties/ZEPHYR_SCALE_SETTINGS.PROJECT.11200?userKey=JIRAUSER38882'
headers = {
                'Authorization': __basic
            }

response = requests.get(url, headers=headers)
soup = BeautifulSoup(response.text, 'html.parser')
element = soup.select('.css-14v6hkl.e16vi2nm1')

print(response.text)

if element:
    print(element[0].text)
else:
    print("Элемент не найден")