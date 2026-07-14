import re
import sys
from os import path

try:
    import numpy as np
except ImportError:
    import subprocess
    subprocess.run('python3 -m pip install numpy', shell=True)
    import numpy as np

RESULT_FILE = 'test/pgbench_result.txt'
PERCENT_LIMIT = 2
VALID_PERCENT = 50

if not path.isfile(RESULT_FILE):
    print(f'Файл не найден: {RESULT_FILE}')
    sys.exit(1)

with open(RESULT_FILE, 'r') as f:
    content = f.read()

# Поддержка форматов PG15+ ("without initial") и старого ("excluding connections")
tps_list = re.findall(
    r'tps\s*=\s*([\d.]+)\s*\((?:without initial connection time|excluding connections establishing)\)',
    content
)

if not tps_list:
    print('TPS-значения не найдены. Проверьте формат файла.')
    sys.exit(1)

tps_values = np.array([int(float(x)) for x in tps_list])
print(f'Всего результатов: {len(tps_values)}')
print(f'Все значения: {[int(v) for v in tps_values]}\n')


def check_value(value, all_values, percent_limit):
    diffs = np.abs((all_values - value) / value * 100)
    return np.sum(diffs <= percent_limit) >= len(all_values) / 2


valid_values   = [int(v) for v in tps_values if check_value(v, tps_values, PERCENT_LIMIT)]
novalid_values = [int(v) for v in tps_values if v not in valid_values]

print(f'Принято  ({len(valid_values)} шт): {valid_values}')
print(f'Отсеяно  ({len(novalid_values)} шт): {novalid_values}\n')

if len(valid_values) >= len(tps_values) * VALID_PERCENT / 100:
    mean_tps = int(np.mean(valid_values))
    spread   = (max(valid_values) - min(valid_values)) / mean_tps * 100
    print(f'Среднее TPS (без аномалий): {mean_tps}')
    print(f'Разброс валидных значений:  {spread:.2f}%')
else:
    print('Нет подходящей группы — тест неудачен, нужно повторить')
