# Массовая загрузка пакетов в devpi (bulk_load)

Инструмент для физического наполнения индекса `root/release` на devpi-сервере —
чтобы стенд переживал офлайн (нет доступа к публичному PyPI).

`bulk_load.py` делает два шага:

1. **Скачивание.** `pip download` тянет указанные пакеты со всеми
   транзитивными зависимостями в локальный wheelhouse. По умолчанию два прохода:
   обычный (колёса) и `--no-binary :none:` (sdist'ы) — чтобы индекс был полезен
   офлайн на разных платформах/версиях Python. Пакеты, которые не скачались,
   логируются и не валят весь прогон.
2. **Загрузка.** Каждый скачанный дистрибутив заливается в devpi через
   devpi-client: `devpi use <url>/root/release`, `devpi login root`,
   затем `devpi upload <distfile>` на каждый файл.

## Требования

- Python 3 с `pip`.
- Установленный `devpi-client` (`pip install devpi-client`).
- Сетевой доступ к devpi-серверу (по умолчанию `http://10.177.103.10:3141`).
- Пароль root в переменной окружения `DEVPI_ROOT_PASSWORD` (в коде не хранится).

## Использование

```bash
export DEVPI_ROOT_PASSWORD='пароль_root'

# Полный инвентарь репозитория (мастер-список) — ПОСТРОЧНО:
# в requirements-all.txt намеренно лежат конфликтующие пины одного пакета
# (разные ветки пинят разные версии), поэтому одним `pip download -r` его
# скачивать нельзя — только построчно через --req-lines.
python3 bulk_load.py --req-lines requirements-all.txt

# Из обычного requirements-файла (без конфликтов версий), один проход -r:
python3 bulk_load.py --req requirements.txt

# Явные пакеты (повторяемо), свой wheelhouse:
python3 bulk_load.py --package requests==2.31.0 --package "numpy>=1.26" --dest ./wheelhouse

# Несколько источников сразу:
python3 bulk_load.py --req base.txt --req extra.txt --package devpi-client

# Под конкретную платформу/версию Python (только колёса в этом проходе):
python3 bulk_load.py --package cryptography \
    --python-version 3.12 --platform manylinux2014_x86_64 --abi cp312 --implementation cp

# Только скачать, без заливки:
python3 bulk_load.py --req requirements.txt --skip-upload

# Залить уже скачанное (повторный прогон без сети):
python3 bulk_load.py --skip-download --dest ./wheelhouse
```

## Параметры

| Флаг                 | Назначение                                                       |
| -------------------- | --------------------------------------------------------------- |
| `--req PATH`         | requirements-файл, один проход `-r` (повторяемый).              |
| `--req-lines PATH`   | requirements-файл, скачиваемый построчно (для мастер-списка).    |
| `--package SPEC`     | Явный спек пакета (повторяемый).                                |
| `--dest DIR`         | Каталог wheelhouse (по умолчанию `./wheelhouse`).               |
| `--devpi-url URL`    | Базовый URL devpi (по умолчанию `http://10.177.103.10:3141`).  |
| `--index NAME`       | Индекс devpi (по умолчанию `root/release`).                     |
| `--root-user NAME`   | Владелец индекса (по умолчанию `root`).                         |
| `--python-version`   | `--python-version` для pip download (напр. `3.12`).            |
| `--platform TAG`     | `--platform` для pip download (повторяемый).                    |
| `--abi`, `--implementation` | соответствующие флаги pip download.                     |
| `--no-sdist`         | Не делать проход за sdist'ами (только колёса).                  |
| `--skip-download`    | Не скачивать, грузить лежащее в `--dest`.                       |
| `--skip-upload`      | Только скачать, не грузить в devpi.                             |

Переменные окружения: `DEVPI_ROOT_PASSWORD` (обязательна для загрузки),
`DEVPI_URL`, `DEVPI_INDEX` (альтернатива одноимённым флагам).

## Замечания

- **Платформенные флаги** (`--platform/--abi/--implementation/--python-version`)
  pip применяет только к проходу за колёсами и требует `--only-binary`, поэтому
  скрипт автоматически добавляет `--only-binary :all:` в этот проход. Проход за
  sdist'ами идёт под текущий интерпретатор без платформенных таргетов.
- **Семантика `devpi upload <file>`.** Используется явная загрузка готовых
  дистрибутивов (devpi-client принимает пути к файлам как аргументы `upload`).
  Если в вашей версии devpi-client этот режим недоступен, перейдите на сборку из
  исходников в каталоге пакета (`devpi upload` без аргументов рядом с
  `setup.py`/`pyproject.toml`) — так делает `../auto_check_new_version/new_release.py`.
- Скачивание идёт **по одной единице** (пакет/req-файл), поэтому сбой одного
  пакета не прерывает остальные; список сбоев печатается в итоговом отчёте.
- Заливка идёт **по одному файлу**, неудачные файлы собираются в список сбоев.
