#!/usr/bin/env python3
import subprocess
import time
import re
import os
import sys
from ftplib import FTP
import configparser

DEVPI_INDEX_URL = 'http://localhost:3141/root/release'

def cmd(command, cwd=None):
    subprocess.run(command, shell=True, check=True, cwd=cwd)

def download_conf(conf_file):
    ftp = FTP('10.177.5.111')
    ftp.login()
    ftp.cwd('stress_reports/stress_test_config')
    with open(conf_file, 'wb') as wf:
        ftp.retrbinary('RETR gitclone.conf', wf.write)
    ftp.quit()
    with open(conf_file, 'r') as r:
        return r.read().strip()

def clone_repo(clone_command, repo_path):
    if os.path.exists(repo_path):
        print(f"Удаляем старый репозиторий: {repo_path}")
        cmd(f'rm -rf {repo_path}')
    print("Клонируем репозиторий...")
    cmd(clone_command)

def version_exists_on_devpi(version):
    try:
        output = subprocess.check_output(
            f'devpi use {DEVPI_INDEX_URL} && devpi list allta=={version}',
            shell=True,
            text=True
        )
        return f'allta {version}' in output
    except subprocess.CalledProcessError:
        return False

def update_version_in_files(repo_path, version):
    setup_py_path = os.path.join(repo_path, 'libs', 'allta', 'setup.py')
    setup_cfg_path = os.path.join(repo_path, 'libs', 'allta', 'setup.cfg')

    with open(setup_py_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    with open(setup_py_path, 'w', encoding='utf-8') as f:
        for line in lines:
            if 'version=' in line:
                line = re.sub(r"version\s*=\s*['\"]\d+\.\d+\.\d+['\"]", f"version='{version}'", line)
            f.write(line)

    if os.path.exists(setup_cfg_path):
        config = configparser.ConfigParser()
        config.read(setup_cfg_path)
        if config.has_section('metadata'):
            config.set('metadata', 'version', version)
            with open(setup_cfg_path, 'w', encoding='utf-8') as f:
                config.write(f)
        else:
            print("⚠️ В setup.cfg нет секции [metadata] — пропускаем замену.")

def upload_version(repo_path):
    password = os.getenv('DEVPI_ADMIN_PASSWORD')
    if not password:
        print("❌ Переменная окружения DEVPI_ADMIN_PASSWORD не установлена!")
        return
    command = (
        f'devpi use {DEVPI_INDEX_URL} && '
        f'devpi login root --password {password} && '
        f'devpi upload --with-docs && '
        f'rm -rf {repo_path}/libs/allta/allta.egg-info && '
        f'rm -rf {repo_path}/libs/allta/dist && '
        f'rm -rf {repo_path}/libs/allta/build'
    )
    cmd(command, cwd=f'{repo_path}/libs/allta')

def clean_and_checkout_branch(repo_path, branch):
    # Переход на HEAD, сброс, очистка и только потом checkout ветки
    cmd('git checkout HEAD', cwd=repo_path)
    cmd('git reset --hard', cwd=repo_path)
    cmd('git clean -fdx', cwd=repo_path)
    cmd(f'git checkout -B {branch} origin/{branch}', cwd=repo_path)

def initial_sync(repo_path, branch='libs'):
    pattern = re.compile(r'^allta_lib v(\d+\.\d+\.\d+)$')

    cmd(f'git fetch origin {branch}', cwd=repo_path)
    clean_and_checkout_branch(repo_path, branch)

    result = subprocess.run(
        ['git', 'log', f'origin/{branch}', '--pretty=format:%H||%s'],
        check=True, capture_output=True, text=True, cwd=repo_path
    )

    versions_handled = set()
    for line in result.stdout.strip().split('\n'):
        if '||' not in line:
            continue
        commit_hash, commit_message = line.strip().split('||', 1)
        match = pattern.match(commit_message.strip())
        if match:
            version = match.group(1)
            if version in versions_handled:
                continue
            if not version_exists_on_devpi(version):
                print(f"🔄 Новая версия {version} не найдена на devpi. Загружаем...")

                try:
                    cmd('git checkout HEAD', cwd=repo_path)
                    cmd('git reset --hard', cwd=repo_path)
                    cmd('git clean -fdx', cwd=repo_path)
                    cmd(f'git checkout {commit_hash}', cwd=repo_path)
                except subprocess.CalledProcessError as e:
                    print(f"❌ Не удалось переключиться на коммит {commit_hash}: {e}")
                    continue

                update_version_in_files(repo_path, version)
                upload_version(repo_path)
            else:
                print(f"✔️ Версия {version} уже есть на devpi. Пропускаем.")
            versions_handled.add(version)

def monitor_branch(repo_path, branch='libs', check_interval=60):
    pattern = re.compile(r'^allta_lib v(\d+\.\d+\.\d+)$')
    last_seen_hash = None
    print(f"👀 Мониторим ветку '{branch}' с интервалом {check_interval} секунд...")
    while True:
        try:
            cmd(f'git fetch origin {branch}', cwd=repo_path)
            clean_and_checkout_branch(repo_path, branch)

            result = subprocess.run(
                ['git', 'log', f'origin/{branch}', '-1', '--pretty=format:%H||%s'],
                check=True, capture_output=True, text=True, cwd=repo_path
            )
            output = result.stdout.strip()
            if '||' not in output:
                print("⚠️ Формат вывода git log нераспознан:", output)
                time.sleep(check_interval)
                continue

            commit_hash, commit_message = output.split('||', 1)
            commit_message = commit_message.strip()
            match = pattern.match(commit_message)
            if match and commit_hash != last_seen_hash:
                version = match.group(1)
                if not version_exists_on_devpi(version):
                    print(f"🆕 Обнаружена новая версия {version}. Загружаем...")

                    try:
                        cmd('git checkout HEAD', cwd=repo_path)
                        cmd('git reset --hard', cwd=repo_path)
                        cmd('git clean -fdx', cwd=repo_path)
                        cmd(f'git checkout {commit_hash}', cwd=repo_path)
                    except subprocess.CalledProcessError as e:
                        print(f"❌ Не удалось переключиться на коммит {commit_hash}: {e}")
                        time.sleep(check_interval)
                        continue

                    update_version_in_files(repo_path, version)
                    upload_version(repo_path)
                else:
                    print(f"✔️ Версия {version} уже существует на devpi.")
                last_seen_hash = commit_hash
            else:
                print("✅ Нет новых подходящих коммитов.")
        except Exception as e:
            print("❌ Ошибка при мониторинге ветки:", e)
        time.sleep(check_interval)

def main():
    base_dir = os.getcwd()
    if base_dir != '/git':
        print("⚠️ Запустите скрипт из директории /git!")
        sys.exit(1)

    conf_file = os.path.join(base_dir, 'gitclone.conf')
    clone_command = download_conf(conf_file)
    repo_path = os.path.join(base_dir, 'stress_test')
    clone_repo(clone_command, repo_path)

    initial_sync(repo_path, branch='libs')
    monitor_branch(repo_path, branch='libs', check_interval=60)

# if __name__ == '__main__':
#     try:
#         main()
#     except KeyboardInterrupt:
#         print("🛑 Остановка по запросу пользователя.")
while True:
    time.sleep(999999999)
    