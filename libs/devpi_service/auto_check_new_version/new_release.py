#!/usr/bin/env python3
import subprocess
import time
import re
import os
import configparser
import json
import base64

DEVPI_INDEX_URL = 'http://10.177.103.10:3141/root/release'
PACKAGE_NAME = 'allta'
BASE_VERSION = os.getenv('DEVPI_BASE_VERSION', '0.0.1')

def cmd(command, cwd=None, env=None):
    subprocess.run(command, shell=True, check=True, cwd=cwd, env=env)

def get_state_path(base_dir):
    state_path = os.getenv('DEVPI_STATE_FILE')
    if state_path:
        return state_path
    if os.path.isdir('/data'):
        return os.path.join('/data', 'devpi_latest_release.json')
    return os.path.join(base_dir, 'devpi_latest_release.json')

def load_state(state_path):
    if not state_path or not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            if isinstance(data, dict):
                return data
    except json.JSONDecodeError:
        pass
    return {}

def get_version_hashes(state):
    versions = state.get('versions')
    if isinstance(versions, dict):
        return {str(k): str(v) for k, v in versions.items() if k and v}
    # backward compatibility with older state format
    latest_version = state.get('latest_version')
    latest_commit = state.get('latest_commit')
    if latest_version and latest_commit:
        return {str(latest_version): str(latest_commit)}
    return {}

def save_state(state_path, data):
    if not state_path:
        return
    state_dir = os.path.dirname(state_path)
    if state_dir and not os.path.exists(state_dir):
        os.makedirs(state_dir, exist_ok=True)
    with open(state_path, 'w', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

def clone_repo(repo_path):
    if os.path.exists(repo_path):
        print(f"Удаляем старый репозиторий: {repo_path}")
        cmd(f'rm -rf {repo_path}')
    print("Клонируем репозиторий...")
    git_token = None
    git_username = None
    token_path = os.getenv('GIT_TOKEN_FILE', 'tokens.json')
    if os.path.exists(token_path):
        try:
            with open(token_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
                git_token = data.get('git_token')
                git_username = data.get('git_username')
        except json.JSONDecodeError:
            # поддержка файла с "сырым" токеном без JSON
            with open(token_path, 'r', encoding='utf-8') as file:
                git_token = file.read().strip()
    if not git_token:
        git_token = os.getenv('GIT_TOKEN')
    if not git_username:
        git_username = os.getenv('GIT_USERNAME')
    if not git_token:
        raise RuntimeError("❌ Не найден git_token в tokens.json или переменной GIT_TOKEN.")

    token = git_token.strip()
    lower_token = token.lower()
    if lower_token.startswith('authorization:'):
        extra_header = token
    elif lower_token.startswith('bearer'):
        value = token[len('bearer'):].lstrip(' :')
        if not value:
            raise RuntimeError("❌ Некорректный токен: после 'Bearer' нет значения.")
        extra_header = f'Authorization: Bearer {value}'
    elif lower_token.startswith('basic'):
        value = token[len('basic'):].lstrip(' :')
        if not value:
            raise RuntimeError("❌ Некорректный токен: после 'Basic' нет значения.")
        extra_header = f'Authorization: Basic {value}'
    elif git_username:
        credentials = f'{git_username}:{token}'.encode('utf-8')
        basic = base64.b64encode(credentials).decode('ascii')
        extra_header = f'Authorization: Basic {basic}'
    else:
        extra_header = f'Authorization: Bearer {token}'

    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GIT_ASKPASS'] = '/bin/false'
    env['SSH_ASKPASS'] = '/bin/false'
    subprocess.run(
        [
            'git',
            '-c', f'http.extraHeader={extra_header}',
            'clone', 'https://git.astralinux.ru/scm/qa/stress_test.git'
        ],
        check=True,
        env=env
    )
    subprocess.run(
        ['git', '-C', repo_path, 'config', 'http.extraHeader', extra_header],
        check=True,
        env=env
    )

def version_exists_on_devpi(version):
    use_result = subprocess.run(
        ['devpi', 'use', DEVPI_INDEX_URL],
        capture_output=True,
        text=True
    )
    if use_result.returncode != 0:
        print(f"⚠️ Не удалось выполнить devpi use: {use_result.stderr.strip()}")
        return False

    result = subprocess.run(
        ['devpi', 'list', f'{PACKAGE_NAME}=={version}'],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        return False

    output = result.stdout.strip()
    if not output:
        return False

    pattern = rf'\b{re.escape(PACKAGE_NAME)}\s*(?:==|\s)\s*{re.escape(version)}\b'
    return re.search(pattern, output) is not None or version in output

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
    upload_docs_env = os.getenv('DEVPI_UPLOAD_DOCS', '1').strip().lower()
    docs_flag = '--with-docs'
    if upload_docs_env in ('0', 'false', 'no', 'off'):
        docs_flag = '--no-docs'
    command = (
        f'devpi use {DEVPI_INDEX_URL} && '
        f'devpi login root --password {password} && '
        f'devpi upload {docs_flag} && '
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

def iter_version_commits(repo_path, branch):
    result = subprocess.run(
        ['git', 'log', f'origin/{branch}', '--pretty=format:%H||%s'],
        check=True, capture_output=True, text=True, cwd=repo_path
    )

    pattern = re.compile(rf'^{re.escape(PACKAGE_NAME)}_lib v(\d+\.\d+\.\d+)$')
    commits = []
    for line in result.stdout.strip().split('\n'):
        if not line or '||' not in line:
            continue
        commit_hash, commit_message = line.strip().split('||', 1)
        commit_message = commit_message.strip()
        match = pattern.match(commit_message)
        if match:
            commits.append((commit_hash, match.group(1)))
    return commits

def find_version_commit(repo_path, branch, version):
    pattern = re.compile(rf'^{re.escape(PACKAGE_NAME)}_lib v{re.escape(version)}$')
    result = subprocess.run(
        ['git', 'log', f'origin/{branch}', '--reverse', '--pretty=format:%H||%s'],
        check=True, capture_output=True, text=True, cwd=repo_path
    )
    for line in result.stdout.strip().split('\n'):
        if not line or '||' not in line:
            continue
        commit_hash, commit_message = line.strip().split('||', 1)
        if pattern.match(commit_message.strip()):
            return commit_hash
    return None

def reset_to_base_version(repo_path, branch, base_version):
    base_hash = find_version_commit(repo_path, branch, base_version)
    if not base_hash:
        print(f"⚠️ Не найден коммит версии {base_version}. Пропускаем сброс.")
        return
    cmd('git checkout HEAD', cwd=repo_path)
    cmd('git reset --hard', cwd=repo_path)
    cmd('git clean -fdx', cwd=repo_path)
    cmd(f'git checkout {base_hash}', cwd=repo_path)

def initial_sync(repo_path, branch='libs', state_path=None):
    cmd(f'git fetch origin {branch}', cwd=repo_path)
    clean_and_checkout_branch(repo_path, branch)

    commits = iter_version_commits(repo_path, branch)
    if not commits:
        reset_to_base_version(repo_path, branch, BASE_VERSION)
        return

    state = load_state(state_path)
    version_hashes = get_version_hashes(state)

    versions_handled = set()
    missing_found = False
    for commit_hash, version in reversed(commits):
        if version in versions_handled:
            continue
        stored_hash = version_hashes.get(version)
        needs_reupload = stored_hash is not None and stored_hash != commit_hash
        exists = version_exists_on_devpi(version)
        should_upload = needs_reupload or not exists

        if should_upload:
            missing_found = True
            if needs_reupload:
                print(f"♻️ Версия {version} изменилась (хеш другой). Пере-загружаем...")
            else:
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
            try:
                upload_version(repo_path)
                version_hashes[version] = commit_hash
            except subprocess.CalledProcessError as e:
                print(f"❌ Ошибка загрузки версии {version}: {e}")
        else:
            print(f"✔️ Версия {version} уже есть на devpi. Пропускаем.")
            version_hashes[version] = commit_hash
        versions_handled.add(version)

    if not missing_found:
        print("✅ Нет новых подходящих коммитов.")

    save_state(state_path, {'versions': version_hashes})

    reset_to_base_version(repo_path, branch, BASE_VERSION)

def monitor_branch(repo_path, branch='libs', check_interval=60, state_path=None):
    print(f"👀 Мониторим ветку '{branch}' с интервалом {check_interval} секунд...")
    while True:
        try:
            cmd(f'git fetch origin {branch}', cwd=repo_path)
            clean_and_checkout_branch(repo_path, branch)

            commits = iter_version_commits(repo_path, branch)
            if not commits:
                print("⚠️ Не найдено релизных коммитов.")
                reset_to_base_version(repo_path, branch, BASE_VERSION)
                time.sleep(check_interval)
                continue

            state = load_state(state_path)
            version_hashes = get_version_hashes(state)

            versions_handled = set()
            missing_found = False
            for commit_hash, version in reversed(commits):
                if version in versions_handled:
                    continue
                stored_hash = version_hashes.get(version)
                needs_reupload = stored_hash is not None and stored_hash != commit_hash
                exists = version_exists_on_devpi(version)
                should_upload = needs_reupload or not exists

                if should_upload:
                    missing_found = True
                    if needs_reupload:
                        print(f"♻️ Версия {version} изменилась (хеш другой). Пере-загружаем...")
                    else:
                        print(f"🆕 Обнаружена новая версия {version}. Загружаем...")

                    try:
                        cmd('git checkout HEAD', cwd=repo_path)
                        cmd('git reset --hard', cwd=repo_path)
                        cmd('git clean -fdx', cwd=repo_path)
                        cmd(f'git checkout {commit_hash}', cwd=repo_path)
                    except subprocess.CalledProcessError as e:
                        print(f"❌ Не удалось переключиться на коммит {commit_hash}: {e}")
                        raise

                    update_version_in_files(repo_path, version)
                    try:
                        upload_version(repo_path)
                        version_hashes[version] = commit_hash
                    except subprocess.CalledProcessError as e:
                        print(f"❌ Ошибка загрузки версии {version}: {e}")
                else:
                    print(f"✔️ Версия {version} уже существует на devpi.")
                    version_hashes[version] = commit_hash
                versions_handled.add(version)

            if not missing_found:
                print("✅ Нет новых подходящих коммитов.")

            save_state(state_path, {'versions': version_hashes})

            reset_to_base_version(repo_path, branch, BASE_VERSION)
        except Exception as e:
            print("❌ Ошибка при мониторинге ветки:", e)
        time.sleep(check_interval)

def main():
    base_dir = os.getcwd()
    state_path = get_state_path(base_dir)

    repo_path = os.path.join(base_dir, 'stress_test')
    clone_repo(repo_path)

    initial_sync(repo_path, branch='libs', state_path=state_path)
    monitor_branch(repo_path, branch='libs', check_interval=60, state_path=state_path)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("🛑 Остановка по запросу пользователя.")
