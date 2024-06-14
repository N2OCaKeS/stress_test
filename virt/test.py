# import re
# import pandas as pd
# from virt_conf import STEP, LOW_COPIES, HIGH_COPIES, UB_RESULT_HTML
# from libs.virtlib import cmd
# import os

# path = '/home/u/git/stress_test/virt/test_results/'
# arh_name = 'result_testvm1.zip'
# cmd(f'cd {path} && unzip {arh_name}')

# files = os.listdir(path)
# file_name = [name for name in files if all(x not in name for x in ['log', 'zip', 'info', 'log'])]
             

# with open(f'{path}{file_name[0]}', 'r') as r:
#     text = r.readlines()


# keys = [' '.join(line.split(' ')[5:7]) for line in text if 'running' in line]
# values = [line.split(' ')[-1].strip() for line in text if 'Score' in line]

# results = {k: v for k, v in zip(keys, values)}

# print(results)


# df = pd.DataFrame(results, index=['Total score']).T

# print(df)  

# df.to_html(UB_RESULT_HTML)



#s = './Run ' + ' '.join([f'-c {copy}' for copy in range(LOW_COPIES, HIGH_COPIES, STEP)])

#print(s)



import os
import time
from multiprocessing import Process, Pipe
import numpy as np


def ping_pong():
    def __conn(connection):
        while True:
            data = connection.recv()
            if data == 'ping':
                #print('Ping reciev, send pong')
                connection.send('pong')
            else: break
        connection.close()

    parent_conn, child_conn = Pipe()
    p = Process(target=__conn, args=(child_conn,))
    p.start()

    start_time = time.time()
    exchange_count = 0

    while time.time() - start_time < 1:
        parent_conn.send('ping')
        parent_conn.recv()
        exchange_count += 1

    print('Exchange per second', exchange_count)
    parent_conn.send('stop')
    p.join()

    return exchange_count

dates = np.array([ping_pong() for i in range(30)])
print(dates)

#Лимит группы по количеству элементов, принимаемой к расчетам, в %
valid_values_percent = 50
#Лимит отклонения, в %
percent_limit = 2 #1.5

def check_value(value, all_values, percent_limit):
    diffs = np.abs((all_values - value) / value * 100)
    return np.sum(diffs <= percent_limit) >= len(all_values) / 2


valid_values = [value for value in dates if check_value(value, dates, percent_limit)]
novalid_values = [value for value in dates if value not in valid_values]
print('Используемые в расчетах значения:', valid_values)
print('Отсеянные значения:', novalid_values)

if len(valid_values) >= len(dates) * valid_values_percent / 100:
    mean_cleaned = np.mean(valid_values)
    print(f"Среднее значение без учета аномалий: {int(mean_cleaned)}")
else:
    print('Нет подходящих групп значений для расчета среднего')

