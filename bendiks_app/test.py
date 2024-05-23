# from collections import defaultdict
# import pandas as pd
# from backup_image_conf import testname_columns
# from numpy import where

# dates_list = [[['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXFAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT2', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT3', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT4', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. FAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. XFS', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'syslog-ng benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'FIO benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'Steal time', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'freeipa authentication test', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark balance', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark kernels', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark vanilla', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXFAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT2', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT3', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT4', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. FAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. XFS', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'syslog-ng benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'FIO benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'Steal time', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'freeipa authentication test', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark balance', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark kernels', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark vanilla', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'Apache_ReverseProxy', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'Parsec impact fs benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'Parsec impact fs benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'auditd benchmark. fileaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'auditd benchmark. psaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'auditd benchmark. useraud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT4 parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'file system benchmark. XFS parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark smol', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'Apache_ReverseProxy', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'Parsec impact fs benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'Parsec impact fs benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'auditd benchmark. fileaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'auditd benchmark. psaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'auditd benchmark. useraud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT4 parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'file system benchmark. XFS parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark smol', 'NOT_EXECUTED']]

# data = defaultdict(list)
# data['Версия'] = [dates_list[0][0][0]]
# data['Ядро'] = [dates_list[0][0][2]]
# data['Режим'] = [dates_list[0][0][1]]
# data['№ стенда'] = [dates_list[0][0][3]]
# new_tab = pd.DataFrame(data=data)

# #Заполняем новый фрейм данными из таблицы
# def add_columns_rows(iter):
#     '''
#     Функция добавляет столбец при совпадении элементов в первой паре словаря и 
#     добавляет строку при совпадении элементов второй пары словаря или 
#     несовпадении в первой паре 
#     '''
#     global new_tab 
#     if [dates_list[iter][0][0]] == list(data.values())[0] and [dates_list[iter][0][2]] == list(data.values())[1] \
#     and [dates_list[iter][0][1]] == list(data.values())[2] and [dates_list[iter][0][3]] == list(data.values())[3]:
#         if dates_list[iter][1] in new_tab.columns:
#             new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
#         else:
#             new_tab.insert(loc=len(new_tab.columns), column=dates_list[iter][1], value='')
#             new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
#     else:
#         data['Версия'] = [dates_list[iter][0][0]]
#         data['Ядро'] = [dates_list[iter][0][2]]
#         data['Режим'] = [dates_list[iter][0][1]]
#         data['№ стенда'] = [dates_list[iter][0][3]]
#         new_tab = new_tab._append(data, ignore_index=True)
#         if dates_list[iter][1] in new_tab.columns:
#             new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
#         else:
#             new_tab.insert(loc=len(new_tab.columns), column=dates_list[iter][1], value='')
#             new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
# [add_columns_rows(item) for item in range(0, len(dates_list))]

# print(new_tab.T)


# #Наводим красоту
# new_tab.fillna('', inplace=True)
# columns = ['Версия', 'Ядро', 'Режим', '№ стенда']
# for col in columns:
#     new_tab[col] = new_tab[col].astype(str).str.replace(r'\[|\]|\'', '', regex=True)



# for k, v in testname_columns.items():
#     new_tab.rename(columns={k:v}, inplace=True)
# for name in new_tab.columns:
#     new_tab[name] = where(new_tab[name] == 'NOT_EXECUTED', 'Не запускался', new_tab[name])
#     new_tab[name] = where(new_tab[name] == 'IN_PROGRESS', 'Выполняется', new_tab[name])
#     new_tab[name] = where(new_tab[name] == 'PASS', 'Выполнено', new_tab[name])
#     new_tab[name] = where(new_tab[name] == 'FAIL', 'Провалено', new_tab[name])



# new_tab = new_tab.sort_values(by=['Режим', '№ стенда'], ascending=[True, True])
# new_tab = new_tab[[x for x in new_tab if x not in new_tab.columns[4:].sort_values()] 
#                 + [x for x in new_tab.columns[4:].sort_values() if x in new_tab]]

# new_tab = new_tab.T
# new_tab.columns = pd.MultiIndex.from_tuples(zip(new_tab.columns, new_tab.iloc[0]))
# new_tab = new_tab[1:]
# print(new_tab)
#test

import psycopg2

def add_value(value):
    conn = psycopg2.connect(host='127.0.0.1',
                            database='b_config',
                            user='postgres',
                            password='1'
                            )

    cursor = conn.cursor()
    values = list(value)

    if len(values) > 1:
        for value in values:
            insert_query = f"INSERT INTO main_table (release_version) VALUES ('{value}')"
            cursor.execute(insert_query)
    else:
        insert_query = f"INSERT INTO main_table (release_version) VALUES ('{value}')"
        cursor.execute(insert_query)

    conn.commit()
    cursor.close()
    conn.close()


#add_value('5')

import json
# from backup_image_conf import releases_dict, rc_list, releases_list, release_version, cycle_tree_index, releases, kernels
# from backup_image_command import cz_comm

def write_bendiks_conf(data):
    # data = {'releases_dict':releases_dict,
    #         'rc_list':rc_list,
    #         'releases_list':releases_list,
    #         'release_version':release_version,
    #         'cycle_tree_index':cycle_tree_index,
    #         'releases':releases,
    #         'cz_comm':cz_comm,
    #         'kernels':kernels}

    with open('./bendiks_conf.json', 'w') as w:
        json.dump(data, w, indent=4)



#write_bendiks_conf()




def mod_bendiks_conf(value):
    with open('./bendiks_conf.json', 'r') as r:
        data = json.load(r)

    stands = {'LowServer':'10.177.103.204',
              'MiddleServer':'10.177.103.203'}
    cz_name = 'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "{}" \
                -l ru_RU.UTF-8 startdisk restore {}-{}rc{} nvme0n1'

    if value not in data['releases_dict'].keys():
        data['releases_dict'][value] = value
    if value not in data['rc_list']:
        data['rc_list'].append(value)
    if '.'.join(value.split('.')[:3]) not in data['releases_list']:
        data['releases_list'].append('.'.join(value.split('.')[:3]))
    if value not in data['release_version']:
        data['release_version'].append(value)
    if value not in data['releases']:
        data['releases'].append(value)        

    if value not in data['cz_comm']['stand3'].keys():
        data['cz_comm']['stand3'][value] = cz_name.format(stands['LowServer'],
                                                          'LowServer',
                                                          ''.join(value.split('.')[:3]),
                                                          ''.join(value.split('.')[3:]))
    if value not in data['cz_comm']['stand4'].keys():
        data['cz_comm']['stand4'][value] = cz_name.format(stands['MiddleServer'],
                                                          'MiddleServer',
                                                          ''.join(value.split('.')[:3]),
                                                          ''.join(value.split('.')[3:]))

    write_bendiks_conf(data)



#mod_bendiks_conf('1.8.1.3')

#print(''.join('1.8.1.3'.split('.')[3:]))


with open('ChangeLog', 'r') as r:
    version = r.readline()
    upp_version = int(version.split(' ')[2].split('.')[-1]) + 1
    pre_version = '.'.join(version.split(' ')[2].split('.')[:-1])
    new_version = f'{' '.join(version.split(' ')[:-1])} {pre_version}.{upp_version}'

    print(version)
    print(new_version)
    print('.'.join(version.split(' ')[2].split('.')[:-1]))