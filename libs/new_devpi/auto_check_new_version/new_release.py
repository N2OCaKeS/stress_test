#!/usr/bin/env python3
import subprocess
import time
import re
import os
import configparser
import json
import base64
import ssl
import shlex
import urllib.request
import urllib.error

DEVPI_RELEASE_INDEX_URL = os.getenv('DEVPI_INDEX_URL') or os.getenv('DEVPI_RELEASE_INDEX_URL') or (
    f"http://localhost:{os.getenv('DEVPI_PORT', '3141')}/root/"
    f"{os.getenv('DEVPI_RELEASE_INDEX', 'release')}"
)
DEVPI_TEST_INDEX_URL = os.getenv('DEVPI_TEST_INDEX_URL') or (
    f"http://localhost:{os.getenv('DEVPI_PORT', '3141')}/root/"
    f"{os.getenv('DEVPI_TEST_INDEX', 'test')}"
)
DEVPI_UPLOAD_USER = os.getenv('DEVPI_UPLOAD_USER', 'allta')
DEVPI_UPLOAD_PASSWORD = os.getenv('DEVPI_UPLOAD_PASSWORD')
PACKAGE_NAME = 'allta'
BASE_VERSION = os.getenv('DEVPI_BASE_VERSION', '0.0.1')
MONITOR_BRANCH = os.getenv('DEVPI_GIT_BRANCH', 'libs')
CLONE_RETRY_INTERVAL = int(os.getenv('DEVPI_CLONE_RETRY_INTERVAL', '60'))
RELEASE_VERSION_PATTERN = r'\d+\.\d+\.\d+'
DEV_VERSION_PATTERN = r'\d+(?:\.\d+)*'
DEV_FOUR_PART_VERSION_PATTERN = r'\d+\.\d+\.\d+\.\d+'

def cmd(command, cwd=None, env=None):
    subprocess.run(command, shell=True, check=True, cwd=cwd, env=env)

def run_checked(command, cwd=None, env=None, error_message=None):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode == 0:
        return result
    stderr = (result.stderr or '').strip()
    stdout = (result.stdout or '').strip()
    details = stderr or stdout or f'exit code {result.returncode}'
    if len(details) > 1200:
        details = details[-1200:]
    raise RuntimeError(f"{error_message or 'Команда завершилась с ошибкой'}: {details}")

def _verify_tls_enabled():
    flag = os.getenv('ALLTA_API_VERIFY_TLS', '0').strip().lower()
    return flag in ('1', 'true', 'yes', 'on')

def fetch_git_token():
    """Берём git-токен из config_api. Единственный источник: ни файлов, ни env."""
    base_url = os.getenv('ALLTA_CONFIG_API_URL')
    api_token = os.getenv('ALLTA_API_TOKEN')
    token_name = os.getenv('DEVPI_GIT_TOKEN_NAME') or 'git_token'

    if not base_url:
        raise RuntimeError("❌ Не задан ALLTA_CONFIG_API_URL — неоткуда взять git-токен.")
    if not api_token:
        raise RuntimeError("❌ Не задан ALLTA_API_TOKEN — нет авторизации для config_api.")

    url = f"{base_url.rstrip('/')}/api/config/v1/config/tokens/details/{token_name}"
    request = urllib.request.Request(url, method='GET')
    request.add_header('Authorization', f'Bearer {api_token}')
    request.add_header('Accept', 'application/json')

    if _verify_tls_enabled():
        context = ssl.create_default_context()
    else:
        context = ssl._create_unverified_context()

    try:
        with urllib.request.urlopen(request, context=context, timeout=30) as response:
            raw = response.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"❌ config_api вернул ошибку {exc.code} при запросе токена '{token_name}': {exc.reason}"
        )
    except urllib.error.URLError as exc:
        raise RuntimeError(f"❌ Не удалось подключиться к config_api ({url}): {exc.reason}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"❌ config_api вернул не-JSON ответ: {exc}")

    if not isinstance(data, dict):
        raise RuntimeError("❌ Неожиданный формат ответа config_api при запросе токена.")

    git_token = data.get('token') or data.get('git_token') or data.get('value')
    git_username = data.get('username') or data.get('git_username') or data.get('login')

    if not git_token:
        raise RuntimeError(
            f"❌ В ответе config_api нет значения токена для '{token_name}'."
        )

    return git_token, git_username

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

def state_key(version, channel):
    if channel == 'dev':
        return f'dev:{version}'
    return version

def target_index_url(channel):
    if channel == 'dev':
        return DEVPI_TEST_INDEX_URL
    return DEVPI_RELEASE_INDEX_URL

def parse_version_commit_message(commit_message):
    """Return (version, channel) for supported allta_lib commit messages."""
    release_pattern = re.compile(
        rf'^{re.escape(PACKAGE_NAME)}_lib v(?P<version>{RELEASE_VERSION_PATTERN})$'
    )
    match = release_pattern.match(commit_message)
    if match:
        return match.group('version'), 'release'

    dev_prefixed_pattern = re.compile(
        rf'^dev\s+{re.escape(PACKAGE_NAME)}_lib v(?P<version>{DEV_VERSION_PATTERN})(?:\s+.*)?$'
    )
    match = dev_prefixed_pattern.match(commit_message)
    if match:
        return match.group('version'), 'dev'

    dev_four_part_pattern = re.compile(
        rf'^{re.escape(PACKAGE_NAME)}_lib v(?P<version>{DEV_FOUR_PART_VERSION_PATTERN})(?:\s+.*)?$'
    )
    match = dev_four_part_pattern.match(commit_message)
    if match:
        return match.group('version'), 'dev'

    return None

def save_state(state_path, data):
    if not state_path:
        return
    state_dir = os.path.dirname(state_path)
    if state_dir and not os.path.exists(state_dir):
        os.makedirs(state_dir, exist_ok=True)
    with open(state_path, 'w', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

def clone_repo(repo_path, branch):
    if os.path.exists(repo_path):
        print(f"Удаляем старый репозиторий: {repo_path}")
        cmd(f'rm -rf {repo_path}')
    print(f"Клонируем ветку '{branch}' репозитория...")

    git_token, git_username = fetch_git_token()

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
    run_checked(
        [
            'git',
            '-c', f'http.extraHeader={extra_header}',
            'clone',
            '--branch', branch,
            '--single-branch',
            'https://git.astralinux.ru/scm/qa/stress_test.git',
            repo_path,
        ],
        env=env,
        error_message=f"Не удалось клонировать ветку '{branch}'"
    )
    run_checked(
        ['git', '-C', repo_path, 'config', 'http.extraHeader', extra_header],
        env=env,
        error_message='Не удалось сохранить git extraHeader'
    )

def version_exists_on_devpi(version, index_url=None):
    index_url = index_url or target_index_url('release')
    use_result = subprocess.run(
        ['devpi', 'use', index_url],
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
                line = re.sub(r"version\s*=\s*['\"][^'\"]+['\"]", f"version='{version}'", line)
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

def _set_setupcfg_docs(repo_path, enabled):
    """Управляет сборкой доков через [devpi:upload] with_docs в setup.cfg.

    У devpi upload нет флага --no-docs, поэтому единственный способ не собирать
    sphinx-доки (которые могут падать и срывать upload) — убрать with_docs из
    настроек пакета.
    """
    cfg_path = os.path.join(repo_path, 'libs', 'allta', 'setup.cfg')
    if not os.path.exists(cfg_path):
        return
    config = configparser.ConfigParser()
    config.read(cfg_path)
    if not config.has_section('devpi:upload'):
        return
    if enabled:
        config.set('devpi:upload', 'with_docs', '1')
    else:
        config.remove_option('devpi:upload', 'with_docs')
    with open(cfg_path, 'w', encoding='utf-8') as f:
        config.write(f)


def upload_version(repo_path, index_url=None):
    index_url = index_url or DEVPI_RELEASE_INDEX_URL
    user = DEVPI_UPLOAD_USER or 'root'
    password = DEVPI_UPLOAD_PASSWORD
    if not password and user == 'root':
        password = os.getenv('DEVPI_ROOT_PASSWORD') or os.getenv('DEVPI_ADMIN_PASSWORD')
    if not password:
        print(f"❌ Пароль для пользователя devpi '{user}' не установлен!")
        return
    upload_docs_env = os.getenv('DEVPI_UPLOAD_DOCS', '1').strip().lower()
    docs_enabled = upload_docs_env not in ('0', 'false', 'no', 'off')
    _set_setupcfg_docs(repo_path, docs_enabled)
    docs_flag = '--with-docs' if docs_enabled else ''
    command = (
        f'devpi use {shlex.quote(index_url)} && '
        f'devpi login {shlex.quote(user)} --password {shlex.quote(password)} && '
        f'devpi upload {docs_flag} && '
        f'rm -rf {shlex.quote(repo_path)}/libs/allta/allta.egg-info && '
        f'rm -rf {shlex.quote(repo_path)}/libs/allta/dist && '
        f'rm -rf {shlex.quote(repo_path)}/libs/allta/build'
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

    commits = []
    for line in result.stdout.strip().split('\n'):
        if not line or '||' not in line:
            continue
        commit_hash, commit_message = line.strip().split('||', 1)
        commit_message = commit_message.strip()
        parsed = parse_version_commit_message(commit_message)
        if parsed:
            version, channel = parsed
            commits.append((commit_hash, version, channel))
    return commits

def find_version_commit(repo_path, branch, version):
    result = subprocess.run(
        ['git', 'log', f'origin/{branch}', '--reverse', '--pretty=format:%H||%s'],
        check=True, capture_output=True, text=True, cwd=repo_path
    )
    for line in result.stdout.strip().split('\n'):
        if not line or '||' not in line:
            continue
        commit_hash, commit_message = line.strip().split('||', 1)
        parsed = parse_version_commit_message(commit_message.strip())
        if parsed and parsed[0] == version and parsed[1] == 'release':
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
    for commit_hash, version, channel in reversed(commits):
        version_key = state_key(version, channel)
        if version_key in versions_handled:
            continue
        index_url = target_index_url(channel)
        stored_hash = version_hashes.get(version_key)
        needs_reupload = stored_hash is not None and stored_hash != commit_hash
        exists = version_exists_on_devpi(version, index_url=index_url)
        should_upload = needs_reupload or not exists

        if should_upload:
            missing_found = True
            if needs_reupload:
                print(f"♻️ Версия {version} изменилась (хеш другой). Пере-загружаем в {index_url}...")
            else:
                print(f"🔄 Новая версия {version} не найдена на devpi. Загружаем в {index_url}...")

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
                upload_version(repo_path, index_url=index_url)
                version_hashes[version_key] = commit_hash
            except subprocess.CalledProcessError as e:
                print(f"❌ Ошибка загрузки версии {version}: {e}")
        else:
            print(f"✔️ Версия {version} уже есть на devpi. Пропускаем.")
            version_hashes[version_key] = commit_hash
        versions_handled.add(version_key)

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
            for commit_hash, version, channel in reversed(commits):
                version_key = state_key(version, channel)
                if version_key in versions_handled:
                    continue
                index_url = target_index_url(channel)
                stored_hash = version_hashes.get(version_key)
                needs_reupload = stored_hash is not None and stored_hash != commit_hash
                exists = version_exists_on_devpi(version, index_url=index_url)
                should_upload = needs_reupload or not exists

                if should_upload:
                    missing_found = True
                    if needs_reupload:
                        print(f"♻️ Версия {version} изменилась (хеш другой). Пере-загружаем в {index_url}...")
                    else:
                        print(f"🆕 Обнаружена новая версия {version}. Загружаем в {index_url}...")

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
                        upload_version(repo_path, index_url=index_url)
                        version_hashes[version_key] = commit_hash
                    except subprocess.CalledProcessError as e:
                        print(f"❌ Ошибка загрузки версии {version}: {e}")
                else:
                    print(f"✔️ Версия {version} уже существует на devpi.")
                    version_hashes[version_key] = commit_hash
                versions_handled.add(version_key)

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
    while True:
        try:
            clone_repo(repo_path, MONITOR_BRANCH)
            break
        except Exception as exc:
            print(f"❌ Ошибка клонирования репозитория: {exc}")
            print(f"⏳ Повтор через {CLONE_RETRY_INTERVAL} секунд...")
            time.sleep(CLONE_RETRY_INTERVAL)

    initial_sync(repo_path, branch=MONITOR_BRANCH, state_path=state_path)
    monitor_branch(repo_path, branch=MONITOR_BRANCH, check_interval=60, state_path=state_path)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("🛑 Остановка по запросу пользователя.")
