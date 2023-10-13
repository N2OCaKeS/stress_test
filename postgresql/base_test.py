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


def test_run(clients):
    cmd(f'sudo bash start_test.sh {clients}')
    cmd('cat test/pgbench_result.txt | grep including | awk \'{print$3}\' >> result_testing.txt')

    if path.isfile('result_testing.txt'):
        with open('result_testing.txt', 'r') as r:
            raw_results = r.read().split('\n')
            tps_values = np.array([int(float(x)) for x in raw_results if x.replace('.', '', 1).isdigit()])
    else: print('Файл с результатами отсутствует')
    print(f'Общий список всех результатов:\n{tps_values}')


    # вычисление сырых z-оценок
    mean = np.mean(tps_values)
    std_dev = np.std(tps_values)
    z_scores_raw = (tps_values - mean) / std_dev
    threshold = 2
    outliers = np.abs(z_scores_raw) > threshold
    not_outliers = np.abs(z_scores_raw) <= threshold
    tps_values_cleaned = tps_values[not_outliers]

    print(f'Аномальные значения: {tps_values[outliers]}')
    mean_cleaned = np.mean(tps_values_cleaned)
    print(f"Среднее значение без учета аномалий: {int(mean_cleaned)}")


test_run(200)
