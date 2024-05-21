from collections import defaultdict
import pandas as pd
from backup_image_conf import testname_columns
from numpy import where

dates_list = [[['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXFAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT2', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT3', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT4', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. FAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'file system benchmark. XFS', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand3'], 'syslog-ng benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'FIO benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'Steal time', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'freeipa authentication test', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark balance', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark kernels', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark vanilla', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXFAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT2', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT3', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT4', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. FAT', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'file system benchmark. XFS', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand3'], 'syslog-ng benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'FIO benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'Steal time', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'freeipa authentication test', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark balance', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark kernels', 'NOT_EXECUTED'], [['1.8.1.2', 'orel', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark vanilla', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'Apache_ReverseProxy', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'Parsec impact fs benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'Parsec impact fs benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'auditd benchmark. fileaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'auditd benchmark. psaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'auditd benchmark. useraud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'file system benchmark. EXT4 parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'file system benchmark. XFS parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.1.82-1-generic', 'stand4'], 'postgresql benchmark smol', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'Apache_ReverseProxy', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'Parsec impact fs benchmark', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'Parsec impact fs benchmark audit-off', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'auditd benchmark. fileaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'auditd benchmark. psaud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'auditd benchmark. useraud', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'file system benchmark. EXT4 parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'file system benchmark. XFS parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand3'], 'linux_system_benchmark. UnixBench parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark parsec', 'NOT_EXECUTED'], [['1.8.1.2', 'smolensk', '6.6.28-1-generic', 'stand4'], 'postgresql benchmark smol', 'NOT_EXECUTED']]

data = defaultdict(list)
data['Версия'] = [dates_list[0][0][0]]
data['Ядро'] = [dates_list[0][0][2]]
data['Режим'] = [dates_list[0][0][1]]
data['№ стенда'] = [dates_list[0][0][3]]
new_tab = pd.DataFrame(data=data)

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
        if dates_list[iter][1] in new_tab.columns:
            new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
        else:
            new_tab.insert(loc=len(new_tab.columns), column=dates_list[iter][1], value='')
            new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
    else:
        data['Версия'] = [dates_list[iter][0][0]]
        data['Ядро'] = [dates_list[iter][0][2]]
        data['Режим'] = [dates_list[iter][0][1]]
        data['№ стенда'] = [dates_list[iter][0][3]]
        new_tab = new_tab._append(data, ignore_index=True)
        if dates_list[iter][1] in new_tab.columns:
            new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
        else:
            new_tab.insert(loc=len(new_tab.columns), column=dates_list[iter][1], value='')
            new_tab.at[new_tab.index[-1], dates_list[iter][1]] = dates_list[iter][2]
[add_columns_rows(item) for item in range(0, len(dates_list))]

print(new_tab.T)


#Наводим красоту
new_tab.fillna('', inplace=True)
columns = ['Версия', 'Ядро', 'Режим', '№ стенда']
for col in columns:
    new_tab[col] = new_tab[col].astype(str).str.replace(r'\[|\]|\'', '', regex=True)



for k, v in testname_columns.items():
    new_tab.rename(columns={k:v}, inplace=True)
for name in new_tab.columns:
    new_tab[name] = where(new_tab[name] == 'NOT_EXECUTED', 'Не запускался', new_tab[name])
    new_tab[name] = where(new_tab[name] == 'IN_PROGRESS', 'Выполняется', new_tab[name])
    new_tab[name] = where(new_tab[name] == 'PASS', 'Выполнено', new_tab[name])
    new_tab[name] = where(new_tab[name] == 'FAIL', 'Провалено', new_tab[name])



new_tab = new_tab.sort_values(by=['Режим', '№ стенда'], ascending=[True, True])
new_tab = new_tab[[x for x in new_tab if x not in new_tab.columns[4:].sort_values()] 
                + [x for x in new_tab.columns[4:].sort_values() if x in new_tab]]

new_tab = new_tab.T
#new_tab.reset_index(drop=True, inplace=True)
print(new_tab)