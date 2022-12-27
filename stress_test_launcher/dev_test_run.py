#!/usr/bin/python3

import subprocess
import sys
import getpass

from atlassian import Confluence
from dev_test_run_conf import STAND, TEST_SET, START_FILE_MAIN_PLAYBOOK


for ind, value in STAND.items():
    print(f'{ind}: {value}')

while True:
    try:
        hosts = list(map(int, input("\nУкажите номера через ';' на каких стендах запустить: ").replace(" ", "").split(";")))
        break
    except ValueError:
        pass

for ind, value in TEST_SET.items():
    print(f'{ind}: {value}')

run_test_in_stand = []

for i in hosts:
    while True:
        try:
            test_set = list(map(int, input(f"\nУкажите номера через ';' какие тестовые сценарии запустить на {STAND[i]}: ").replace(" ", "").split(";")))
            break
        except ValueError:
            pass
    run_test_in_stand.append({STAND[i]: test_set})

# print(run_test_in_stand)


# Создание main файлов для запуска 

for item in run_test_in_stand:
    for stand, set_test in item.items():
        main_file = open(f'playbooks/{stand}.yml',"w")
        main_file.writelines(START_FILE_MAIN_PLAYBOOK)
        for item_test in set_test:
            main_file = open(f'playbooks/{stand}.yml',"a+")
            main_file.write(f"- include: ./{TEST_SET[item_test]}.yml\n")
            main_file.close()
