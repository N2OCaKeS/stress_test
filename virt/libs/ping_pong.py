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

dates = np.array([ping_pong() for i in range(50)])
print(dates)

#Лимит группы по количеству элементов, принимаемой к расчетам, в %
valid_values_percent = 50
#Лимит отклонения, в %
percent_limit = 30 #1.5

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
    mean_cleaned = 'No results'
    print('Нет подходящих групп значений для расчета среднего')


with open('/home/vagrant/results', 'w') as w:
    w.write(str(round(mean_cleaned), 1))