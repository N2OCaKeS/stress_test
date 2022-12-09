#!/usr/bin/python3

import subprocess
import sys
import getpass

from atlassian import Confluence

LIFE_USERNAME = input("Введите имя пользовтеля от life.astralinux: ")
LIFE_PASSWORD = getpass.getpass("Введите пароль от life.astralinux: ")
CONFLUENCE_SPACE = "~"+LIFE_USERNAME

STAND1 = 'stand1'
STAND2 = 'stand2'

confluence = Confluence(
    url='https://life.astralinux.ru',
    username=LIFE_USERNAME,
    password=LIFE_PASSWORD)

try:
    confluence.get_user_details_by_username(LIFE_USERNAME)
except:
    print("Неправильный логин или пароль!")
    sys.exit(1)
    

SNAPSHOT_STAND1 = input("Введите имя снимка(snapshot) для stand1: ")
SNAPSHOT_STAND2 = input("Введите имя снимка(snapshot) для stand2: ")

log_stand1 = open('stand1.log','w+')
stand1 = subprocess.Popen(f'ansible-playbook playbooks/stand1/main.yml --extra-var "HOST={STAND1} SNAPSHOT={SNAPSHOT_STAND1} USER_LIFE={LIFE_USERNAME} PASSWORD_LIFE={LIFE_PASSWORD} CONFLUENCE_SPACE={CONFLUENCE_SPACE}"', 
                          shell=True, 
                          stdout=log_stand1, 
                          stderr=subprocess.STDOUT)
print("Прогон на stand1 запущен...")

log_stand2 = open('stand2.log', 'w+')
stand2 = subprocess.Popen(f'ansible-playbook playbooks/stand2/main.yml --extra-var "HOST={STAND2} SNAPSHOT={SNAPSHOT_STAND2} USER_LIFE={LIFE_USERNAME} PASSWORD_LIFE={LIFE_PASSWORD} CONFLUENCE_SPACE={CONFLUENCE_SPACE}"', 
                          shell=True, 
                          stdout=log_stand2, 
                          stderr=subprocess.STDOUT)
print("Прогон на stand2 запущен...")

stand1.wait()
log_stand1.close()

stand2.wait()
log_stand2.close()