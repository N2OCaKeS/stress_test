#!/bin/python3
import subprocess
from os import path

def cmd(command):
    subprocess.run(command, shell=True)

try:
    import numpy as np
except (ImportError, ImportWarning):
    cmd('sudo apt-get install -y python3-numpy')
    import numpy as np

#Создание и настройка БД
cmd('sudo bash default_base_up.sh')


def test_run(clients, repeat):
    repeat_list = [str(clients) for i in range(repeat)]
    repeat_str = ' '.join(repeat_list)

    cmd(f'sudo bash start_test.sh "{repeat_str}"')
    cmd('cat test/pgbench_result.txt | grep including | awk \'{print$3}\' >> result_testing.txt')

    if path.isfile('result_testing.txt'):
        with open('result_testing.txt', 'r') as r:
            raw_results = r.read().split('\n')
            tps_values = np.array([int(float(x)) for x in raw_results if x.replace('.', '', 1).isdigit()])
    else: print('Файл с результатами отсутствует')
    print(f'Общий список всех результатов:\n{tps_values}')


    #Лимит группы по количеству элементов, принимаемой к расчетам, в %
    valid_values_percent = 50
    #Лимит погрешности, в %
    percent_limit = 1.5

    def check_value(value, all_values, percent_limit):
        diffs = np.abs((all_values - value) / value * 100)
        return np.sum(diffs <= percent_limit) >= len(all_values) / 2


    valid_values = [value for value in tps_values if check_value(value, tps_values, percent_limit)]
    novalid_values = [value for value in tps_values if value not in valid_values]
    print('Используемые в расчетах значения:', valid_values)
    print('Отсеянные значения:', novalid_values)

    if len(valid_values) >= len(tps_values) * valid_values_percent / 100:
        mean_cleaned = np.mean(valid_values)
        print(f"Среднее значение без учета аномалий: {int(mean_cleaned)}")
    else:
        print('Нет подходящих групп значений для расчета среднего')


test_run(200, 20)


