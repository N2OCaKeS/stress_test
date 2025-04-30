#!/bin/python3
import subprocess
import os
from sys import exit
from ftplib import FTP
import json

def cmd(command):
    subprocess.run([command], shell=True, check=True)


attention_line = '=' * 130
conf_file = os.getcwd() + '/gitclone.conf'
gitclone_cmd = "git clone -c http.extraHeader='Authorization: {}' https://git.astralinux.ru/scm/qa/stress_test.git"

if os.path.isfile('/fastapi_app/src/add_tuning/env.json'):
    with open('fastapi_app/src/add_tuning/env.json', 'r') as creds:
        env = json.load(creds)
    with open(conf_file, 'w') as w:
        w.write(gitclone_cmd.format(env['git_token']))

#Проверяем директорию запуска
if os.getcwd() != '/home/u/git':
    print('\n', '\033[1m\033[33mВнимание!!!\033[0m')
    print(attention_line)
    print(f'Текущая директория {os.getcwd()}')
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

#Скачиваем конфиг
def download_conf():
    ftp = FTP('10.177.5.111')
    ftp.login()
    ftp.cwd('stress_reports/stress_test_config')
    with open(conf_file, 'wb') as wf:
        ftp.retrbinary('RETR gitclone.conf', wf.write)
    ftp.quit()
    with open(conf_file, 'r') as r:
        conf = r.read()
    return conf

#Клонируем гит
#cmd(download_conf())
if os.path.isfile(conf_file):
    with open(conf_file, 'r') as r:
        conf = r.read()
    cmd(conf)
else: raise FileExistsError('File with git clone cmd note found')

#Добавляем конфигурацию pip
pip_conf_path = '/etc/pip.conf'
pip_conf = '''
[global]
# основной индекс
index-url = http://10.177.103.10:3141/root/release
# резервный официальный индекс PyPI
extra-index-url = https://pypi.org/simple
# Указываем что основному индексу можно доверять
trusted-host = 10.177.103.10
'''

with open(pip_conf_path, 'w') as w:
    w.write(pip_conf)
os.chmod(pip_conf_path, 0o644)

