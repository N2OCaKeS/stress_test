#!/usr/bin/env python3
"""Массовая загрузка пакетов в devpi (root/release) для офлайн-выживания.

Скрипт делает две вещи:
  1. Скачивает указанные пакеты со всеми транзитивными зависимостями в
     локальный wheelhouse (`pip download`) — и колёса, и sdist'ы.
  2. Загружает каждый скачанный дистрибутив в индекс devpi через devpi-client.

Источник пакетов — requirements-файлы (--req) и/или явные спеки (--package).
Пароль root берётся из переменной окружения DEVPI_ROOT_PASSWORD (не хардкодим).
"""
import argparse
import os
import subprocess
import sys

# Куда грузим по умолчанию: read-only ACS-хост с devpi.
DEFAULT_DEVPI_URL = 'http://10.177.103.10:3141'
DEFAULT_INDEX = 'root/release'

# Расширения, которые devpi-client принимает как дистрибутивы.
DIST_SUFFIXES = ('.whl', '.tar.gz', '.zip', '.tar.bz2')


def parse_args():
    parser = argparse.ArgumentParser(
        description='Скачать пакеты с зависимостями и залить их в devpi root/release.'
    )
    parser.add_argument('--req', action='append', default=[], metavar='PATH',
                        help='requirements-файл (один проход pip download -r, можно несколько раз).')
    parser.add_argument('--req-lines', action='append', default=[], metavar='PATH',
                        help='requirements-файл, скачиваемый ПОСТРОЧНО: каждый спек — '
                             'отдельный pip download. Нужен для мастер-списка с '
                             'конфликтующими пинами одного пакета.')
    parser.add_argument('--package', action='append', default=[], metavar='SPEC',
                        help='Явный спек пакета, напр. "requests==2.31.0" (повторяемый).')
    parser.add_argument('--dest', default='./wheelhouse',
                        help='Каталог wheelhouse для скачивания (по умолчанию ./wheelhouse).')
    parser.add_argument('--devpi-url', default=os.getenv('DEVPI_URL', DEFAULT_DEVPI_URL),
                        help=f'Базовый URL devpi (по умолчанию {DEFAULT_DEVPI_URL}).')
    parser.add_argument('--index', default=os.getenv('DEVPI_INDEX', DEFAULT_INDEX),
                        help=f'Индекс devpi (по умолчанию {DEFAULT_INDEX}).')
    parser.add_argument('--root-user', default='root',
                        help='Имя пользователя-владельца индекса (по умолчанию root).')
    # Платформенные флаги для pip download. Пустое значение -> флаг не передаётся.
    parser.add_argument('--python-version', default='',
                        help='--python-version для pip download (напр. 3.12). По умолчанию текущий интерпретатор.')
    parser.add_argument('--platform', action='append', default=[], metavar='TAG',
                        help='--platform для pip download (повторяемый, напр. manylinux2014_x86_64).')
    parser.add_argument('--abi', default='',
                        help='--abi для pip download (напр. cp312).')
    parser.add_argument('--implementation', default='',
                        help='--implementation для pip download (напр. cp).')
    parser.add_argument('--no-sdist', action='store_true',
                        help='Не делать второй проход с --no-binary :none: (только колёса).')
    parser.add_argument('--skip-download', action='store_true',
                        help='Пропустить скачивание, грузить уже лежащее в --dest.')
    parser.add_argument('--skip-upload', action='store_true',
                        help='Только скачать, не загружать в devpi.')
    return parser.parse_args()


def pip_download(specs, requirements, dest, args, no_binary):
    """Один проход pip download. Возвращает True при успехе, False при сбое."""
    cmd = [sys.executable, '-m', 'pip', 'download', '--dest', dest]

    # Платформенные таргеты несовместимы со сборкой из исходников, поэтому
    # they применяются только к проходу за колёсами (no_binary=False).
    if not no_binary:
        if args.python_version:
            cmd += ['--python-version', args.python_version]
        for platform in args.platform:
            cmd += ['--platform', platform]
        if args.abi:
            cmd += ['--abi', args.abi]
        if args.implementation:
            cmd += ['--implementation', args.implementation]
        # При указании platform/abi pip требует --only-binary.
        if args.platform or args.abi or args.implementation or args.python_version:
            cmd += ['--only-binary', ':all:']
    else:
        cmd += ['--no-binary', ':none:']

    for req in requirements:
        cmd += ['-r', req]
    cmd += list(specs)

    print('  $', ' '.join(cmd))
    result = subprocess.run(cmd)
    return result.returncode == 0


def read_spec_lines(path):
    """Читает спеки из requirements-файла построчно (без комментариев и опций)."""
    specs = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            text = line.strip()
            if not text or text.startswith('#') or text.startswith('-'):
                continue
            specs.append(text)
    return specs


def download_all(args):
    """Скачивает каждый спек/req по отдельности, чтобы один сбой не валил всё."""
    os.makedirs(args.dest, exist_ok=True)
    failures = []

    # Единицы скачивания: каждый явный пакет — отдельно, каждый req-файл — отдельно,
    # каждая строка из --req-lines — отдельно (так конфликтующие пины не валят resolver).
    units = [([spec], [], spec) for spec in args.package]
    units += [([], [req], f'-r {req}') for req in args.req]
    for req in args.req_lines:
        units += [([spec], [], spec) for spec in read_spec_lines(req)]

    passes = [False] if args.no_sdist else [False, True]
    for specs, reqs, label in units:
        ok_any = False
        for no_binary in passes:
            tag = 'sdist' if no_binary else 'wheels'
            print(f'[download:{tag}] {label}')
            if pip_download(specs, reqs, args.dest, args, no_binary):
                ok_any = True
            else:
                print(f'  ! проход {tag} для "{label}" завершился с ошибкой (продолжаем)')
        if not ok_any:
            failures.append(label)
    return failures


def list_distfiles(dest):
    files = []
    for name in sorted(os.listdir(dest)):
        path = os.path.join(dest, name)
        if os.path.isfile(path) and name.endswith(DIST_SUFFIXES):
            files.append(path)
    return files


def run(cmd):
    print('  $', ' '.join(cmd))
    return subprocess.run(cmd).returncode == 0


def devpi_login(args, password):
    url = f"{args.devpi_url.rstrip('/')}/{args.index}"
    if not run(['devpi', 'use', url]):
        return False
    return run(['devpi', 'login', args.root_user, '--password', password])


def upload_all(args, files):
    """Грузит каждый дистрибутив отдельно. Возвращает (uploaded, failures)."""
    uploaded = 0
    failures = []
    for path in files:
        # Явная загрузка готового дистрибутива: devpi upload <file>.
        if run(['devpi', 'upload', path]):
            uploaded += 1
        else:
            print(f'  ! не удалось загрузить {os.path.basename(path)}')
            failures.append(path)
    return uploaded, failures


def main():
    args = parse_args()

    if not args.package and not args.req and not args.req_lines and not args.skip_download:
        print('Нужен хотя бы один --package / --req / --req-lines.', file=sys.stderr)
        return 2

    download_failures = []
    if not args.skip_download:
        download_failures = download_all(args)
    else:
        print('Скачивание пропущено (--skip-download).')

    files = list_distfiles(args.dest)
    print(f'\nВ wheelhouse найдено дистрибутивов: {len(files)}')

    upload_failures = []
    uploaded = 0
    if args.skip_upload:
        print('Загрузка в devpi пропущена (--skip-upload).')
    elif not files:
        print('Нечего загружать — wheelhouse пуст.')
    else:
        password = os.getenv('DEVPI_ROOT_PASSWORD')
        if not password:
            print('❌ Не задана переменная окружения DEVPI_ROOT_PASSWORD.', file=sys.stderr)
            return 1
        if not devpi_login(args, password):
            print('❌ Не удалось настроить/авторизовать devpi-client.', file=sys.stderr)
            return 1
        uploaded, upload_failures = upload_all(args, files)

    print('\n=== Итог ===')
    print(f'Скачано файлов в wheelhouse: {len(files)}')
    print(f'Загружено в {args.index}: {uploaded}')
    if download_failures:
        print(f'Сбои скачивания ({len(download_failures)}):')
        for item in download_failures:
            print(f'  - {item}')
    if upload_failures:
        print(f'Сбои загрузки ({len(upload_failures)}):')
        for item in upload_failures:
            print(f'  - {os.path.basename(item)}')

    return 0 if not download_failures and not upload_failures else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('🛑 Остановка по запросу пользователя.')
        sys.exit(130)
