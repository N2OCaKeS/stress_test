#!/usr/bin/env python3
import subprocess
import time
import re
import os
import sys
from ftplib import FTP

def cmd(command, cwd=None):
    """
    Выполняет shell-команду.
    """
    subprocess.run(command, shell=True, check=True, cwd=cwd)

def download_conf(conf_file):
    """
    Скачивает конфигурационный файл через FTP.
    Возвращает строку с командой для клонирования репозитория.
    """
    ftp = FTP('10.177.5.111')
    ftp.login()
    ftp.cwd('stress_reports/stress_test_config')
    with open(conf_file, 'wb') as wf:
        ftp.retrbinary('RETR gitclone.conf', wf.write)
    ftp.quit()
    with open(conf_file, 'r') as r:
        conf = r.read().strip()
    return conf

def clone_repo(clone_command, repo_path):
    """
    Если репозиторий уже существует, удаляет его.
    Выполняет клонирование репозитория.
    """
    if os.path.exists(repo_path):
        try:
            print(f"Удаляем старый репозиторий: {repo_path}")
            cmd('rm -rf ' + repo_path)
        except subprocess.CalledProcessError as e:
            print("Ошибка при удалении старого репозитория:", e)
    print("Клонируем репозиторий...")
    cmd(clone_command)

def perform_actions(repo_path, commit_hash, commit_message):
    """
    Здесь выполняются необходимые действия для нового коммита.
    Например, можно запускать сборку, тесты и т.п.
    """
    print(f"Выполняем действия для коммита {commit_hash} с сообщением: '{commit_message}'")
    # Пример: cmd('python3 your_script.py', cwd=repo_path)
    password = os.getenv(''
    '')
    command = f'devpi use http://localhost:3141/root/release && devpi login root --password {password} && devpi upload --with-docs && rm -rf {repo_path}/libs/allta/allta.egg-info && rm -rf {repo_path}/libs/allta/dist && rm -rf {repo_path}/libs/allta/build'
    cmd(command, cwd = f'{repo_path}/libs/allta')


def monitor_branch(repo_path, branch='libs', check_interval=60):
    """
    В бесконечном цикле раз в check_interval (60 сек) проверяет указанную ветку.
    Если обнаружен новый коммит с сообщением вида:
        allta_lib vX.Y.Z   (где X, Y, Z — цифры)
    то происходит переключение на этот коммит и выполняются действия.
    """
    pattern = re.compile(r'^allta_lib v\d+\.\d+\.\d+$')
    last_commit = None
    print(f"Мониторим ветку '{branch}' с интервалом {check_interval} секунд...")
    while True:
        try:
            # Обновляем данные по ветке
            cmd(f'git fetch origin {branch}', cwd=repo_path)
            # Получаем последний коммит ветки (разделитель "||" используется для удобного парсинга)
            result = subprocess.run(
                ['git', 'log', f'origin/{branch}', '-1', '--pretty=format:%H||%s'],
                check=True, capture_output=True, text=True, cwd=repo_path
            )
            output = result.stdout.strip()
            if '||' not in output:
                print("Неожиданный формат вывода git log:", output)
                time.sleep(check_interval)
                continue

            commit_hash, commit_message = output.split('||', 1)
            commit_message = commit_message.strip()

            # Если сообщение соответствует требуемому шаблону и коммит новый – выполняем действия
            if pattern.match(commit_message):
                if commit_hash != last_commit:
                    print(f"Обнаружен новый коммит: {commit_hash} с сообщением: '{commit_message}'")
                    cmd(f'git checkout {commit_hash}', cwd=repo_path)
                    perform_actions(repo_path, commit_hash, commit_message)
                    last_commit = commit_hash
                else:
                    print("Новый коммит не обнаружен (последний уже обработан).")
            else:
                print("Последний коммит не соответствует требуемому формату:", commit_message)
        except subprocess.CalledProcessError as e:
            print("Ошибка при выполнении git-команды:", e)
        except Exception as ex:
            print("Произошла непредвиденная ошибка:", ex)
        time.sleep(check_interval)

def main():
    base_dir = os.getcwd()
    # Если требуется запускать из определённого каталога, можно оставить проверку.
    if base_dir != '/git':
        print("Запустите скрипт из директории /git!")
        sys.exit(1)
    
    conf_file = os.path.join(base_dir, 'gitclone.conf')
    # Скачиваем конфигурацию (содержит команду для клонирования)
    clone_command = download_conf(conf_file)
    repo_path = os.path.join(base_dir, 'stress_test')
    # Клонирование происходит только один раз
    clone_repo(clone_command, repo_path)
    # Запуск мониторинга ветки (раз в 60 сек)
    monitor_branch(repo_path, branch='libs', check_interval=60)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("Остановка по запросу пользователя.")
