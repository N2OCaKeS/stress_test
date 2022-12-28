#!/usr/bin/python3

import subprocess
import sys
import getpass

from atlassian import Confluence
from dev_test_run_conf import STAND, TEST_SET, START_FILE_MAIN_PLAYBOOK


# Выбор стендов

for ind, value in STAND.items():
    print(f'{ind}: {value[1]}{value[0]}\033[0m')

while True:
    try:
        hosts = list(map(int, input("\nУкажите номера через ';' на каких стендах запустить: ").replace(" ", "").split(";")))
        break
    except ValueError:
        pass


# Выбор тестовых сценариев для каждого стенда

for ind, value in TEST_SET.items():
    print(f"{ind}: {value.get('name')}")

run_test_in_stand = []

for i in hosts:
    while True:
        try:
            test_set = list(map(int, input(f"\nУкажите номера через ';' какие тестовые сценарии запустить на {STAND[i][1]}{STAND[i][0]}:\033[0m ").replace(" ", "").split(";")))
            break
        except ValueError:
            pass
    run_test_in_stand.append({STAND[i]: test_set})

# print(run_test_in_stand)

# Создание main файлов для запуска 

for item in run_test_in_stand:
    for stand, set_test in item.items():
        main_file = open(f'playbooks/{stand[0]}.yml',"w")
        main_file.writelines(START_FILE_MAIN_PLAYBOOK)
        for item_test in set_test:
            main_file = open(f'playbooks/{stand[0]}.yml',"a+")
            main_file.write(f"- include: ./{TEST_SET[item_test].get('path')}{TEST_SET[item_test].get('name')}.yml\n")
            main_file.close()


