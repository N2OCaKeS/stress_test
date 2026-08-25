#!/bin/python3
import subprocess
from os import getcwd
from sys import exit
from ftplib import FTP

def cmd(command):
    subprocess.run([command], shell=True, check=True)


attention_line = '=' * 130
conf_file = getcwd() + '/gitclone.conf'
with open(conf_file, 'r') as r:
    conf = r.read()

#Проверяем директорию запуска
if getcwd() != '/home/u/git':
    print('\n', '\033[1m\033[33mВнимание!!!\033[0m')
    print(attention_line)
    print(f'Текущая директория {getcwd()}')
    print('Запустите скрипт из директории /home/u/git')
    print(attention_line, '\n')
    exit(1)

#Удаляем старый гит
try:
    cmd('sudo rm -r /home/u/git/stress_test')
except Exception as e:
    print('\n', '\033[1m\033[33mВнимание!!!\033[0m')
    print(attention_line)
    print(e)
    print(attention_line, '\n')

#Клонируем гит
cmd(conf)