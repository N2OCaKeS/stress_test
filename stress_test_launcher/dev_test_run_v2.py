#!/usr/bin/python3

import sys
import subprocess

from os.path import exists
from os import mkdir, listdir
from getpass import getpass
from atlassian import Confluence
from dev_test_run_conf import STANDS, TEST_SETS, END_COLOR_LINE, START_FILE_MAIN_PLAYBOOK

# Данные от life

# TODO добавить возможность вводить либо токен либо пароль !!!!!!!! TODO

LIFE_USERNAME = input("Введите имя пользовтеля от life.astralinux: ")
LIFE_PASSWORD = getpass("Введите пароль от life.astralinux: ")

confluence = Confluence(
    url='https://life.astralinux.ru',
    username=LIFE_USERNAME,
    password=LIFE_PASSWORD
)

try:
    confluence.get_user_details_by_username(LIFE_USERNAME)
except:
    print("Неправильный логин или пароль!")
    sys.exit(1)

CONFLUENCE_SPACE = input("Введите пространство в life.astralinux: ")


# Выбор стендов
print("-" * 20)
for num_stand, desk_stand in STANDS.items():
    print(f"{num_stand}: {desk_stand.get('color')}{desk_stand.get('name')}{END_COLOR_LINE}")

while True:
    try:
        hosts = list(map(int, input("\nУкажите номера через \033[33m';'\033[0m на каких стендах запустить: ").replace(" ", "").split(";")))
        break
    except ValueError:
        pass

# Выбор тестовых сценариев и резервной копии на каждом выбранном стенде

print("-" * 20)
for num_test_set, desk_test_set in TEST_SETS.items():
    print(f"{num_test_set}: {desk_test_set.get('name')}")

# Массив с словарем на каком стенде какие тесты запустить
run_test_in_stand = []

for num_stand in hosts:
    while True:
        try:
            test_set = list(map(int, input(f"\nУкажите номера через \033[33m';'\033[0m какие тестовые сценарии запустить на {STANDS[num_stand].get('color')}{STANDS[num_stand].get('name')}: {END_COLOR_LINE}").replace(" ", "").split(";")))
            snapshot = input(f"Введите название резервной копии (snapshot) для {STANDS[num_stand].get('color')}{STANDS[num_stand].get('name')}: {END_COLOR_LINE}")
            break
        except ValueError:
            pass
    run_test_in_stand.append(({STANDS[num_stand].get('name'): {'test_set': test_set, 'snapshot': snapshot}}))

# Создание main файлов для запуска
for info_for_start_stand in run_test_in_stand:
    for stand, info_stand in info_for_start_stand.items():
        main_file = open(f'playbooks/{stand}.yml', 'w')
        main_file.writelines(START_FILE_MAIN_PLAYBOOK)
        for item_test in info_stand.get('test_set'):
            main_file = open(f'playbooks/{stand}.yml', "a+")
            main_file.write(f"\n- include: ./{TEST_SETS[item_test].get('path')}{TEST_SETS[item_test].get('name')}.yml")

# Запуск main файлов 

for num_stand in hosts:
    if not exists('logs'):
        mkdir('logs')
    log_file = open(f"logs/{STANDS[num_stand].get('name')}.log", "w+")
    running_stand = subprocess.Popen('sleep 10 && ls /home', shell=True, stdout=log_file, stderr=log_file)