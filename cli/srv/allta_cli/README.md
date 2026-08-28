# Allta_cli

## Документация

Allta_cli - утилита для взаимодействия с сервисами НТ через консоль. Для получения документации выполните команду:

```bash
allta --help
```

## Возможности

1. Клонирование репозитория git
2. Получение токенов
3. Получение releases.json
4. Получение iLO-кредов (`allta ilo`)
5. Работа с сервисными логинами и паролями (`allta creds`)
6. Загрузка и удаление пакетов в devpi `root/pypi` (`allta devpi`)

## Команды без входа

```bash
allta boxes
allta releases
allta login <username> [password]
allta -l <username> [password]
```

## Команды после входа

```bash
allta file releases.json
allta ilo
allta ilo 12
allta creds
allta creds -s nexus
allta tokens git_token
allta git
allta devpi load requests==2.32.3
allta devpi load -r /path/to/req.txt
DEVPI_URL=http://localhost:3141 allta devpi debug -R root/pypi
allta devpi repos
allta devpi list -R root/pypi requests
allta devpi check -R root/release allta
allta devpi download -R root/test allta -d /tmp/wheelhouse
allta devpi install -R root/test -p /usr/bin/python3 allta
allta devpi remove requests==2.32.3 -y
allta devpi remove -r /path/to/req.txt -y
```

## Шорткаты команд

```bash
allta i 12
allta cr nexus
allta t git_token
allta f releases.json
allta g
allta lg user secret -t
allta bx
allta rel
allta py
```

Шорткаты не отображаются как отдельные команды в help. Это просто короткие формы для основных команд.

## Флаги login

```bash
allta login user secret -g
allta login user secret -t
allta login user secret -k git_token
allta login user secret -i
allta login user secret -s 12
allta login user secret -f releases.json
ALLTA_PASSWORD=secret allta login user
ALLTA_API_TOKEN=jwt allta login user --token
allta login --token user
```

Передача пароля в аргументах командной строки остаётся для совместимости, но небезопасна и будет удалена в будущих версиях. Предпочтительные варианты: `ALLTA_PASSWORD`, `ALLTA_API_TOKEN`/`ALLTA_TOKEN` или интерактивный скрытый ввод.

## Devpi команды

```bash
allta devpi load 'requests==2.32.3'
allta devpi load 'requests>=2.32,<3'
allta devpi load -r /path/to/req.txt
allta devpi debug -R root/pypi
allta devpi repos
allta devpi list
allta devpi list -R root/pypi
allta devpi list --repo root/pypi requests
allta devpi list --repo root/pypi requests '>2.32.3' '<2.33.1'
allta devpi list -R root/pypi -r /path/to/req.txt
allta devpi check -R root/release allta
allta devpi download -R root/test allta -d /tmp/wheelhouse
allta devpi install -R root/test -p /usr/bin/python3 allta
allta devpi install -R root/test -p /usr/bin/python3 -r /path/to/req.txt
allta devpi remove requests -y
allta devpi remove 'requests==2.32.3' -y
allta devpi remove -r /path/to/req.txt -y
```

`load` скачивает указанные пакеты из внешнего PyPI вместе с транзитивными зависимостями
и загружает дистрибутивы в `root/pypi`. `allta` намеренно не загружается в `root/pypi`,
он публикуется в `root/release`.

Repo передаётся через `--repo`/`-R`: `root/pypi`, `root/release`, `root/test`,
`user/dev`. Если repo не указан, используется `root/pypi`. Файлы требований всегда
передаются через `--requirement`/`-r`; Python для установки — через `--python`/`-p`.

URL devpi задаётся через `ALLTA_DEVPI_URL` или `DEVPI_URL`. `ALLTA_DEVPI_URL`
имеет приоритет. Если переменные не заданы, используется
`http://allta.devos.astralinux.ru:3141`.
Источник для `allta devpi load` задаётся через `ALLTA_DEVPI_SOURCE_INDEX_URL`
или `DEVPI_SOURCE_INDEX_URL`; по умолчанию используется `https://pypi.org/simple`.
Для локальной отладки allta API с self-signed сертификатом можно задать
`ALLTA_API_VERIFY_TLS=0`.
Проверить эффективные настройки можно так:

```bash
DEVPI_URL=http://localhost:3141 allta devpi debug -R root/pypi
```

Для `root/pypi` CLI сначала ищет config-service credential `devpi_allta`, потому что
upload в этот repo закреплён за локальным пользователем `allta`. Затем пробует текущий
`allta login` token и env fallback.

Для остальных repo сначала используется текущий `allta login`: сохранённый login и token
передаются в `devpi login`, а devpi-плагин проверяет token через Allta Auth API. Затем
идёт fallback на config-service credential `devpi_allta`/`devpi_root` и env
`ALLTA_DEVPI_USERNAME`/`ALLTA_DEVPI_PASSWORD`.

## Автодополнение

CLI поддерживает shell completion для `bash` и `zsh`, включая основные команды, опции и top-level шорткаты (`lg`, `cr`, `bx` и т.д.).

При сборке `.deb` completion-файлы заранее генерируются и попадают в пакет. `bash` completion ставится сразу, а `zsh` completion копируется в системный каталог только если `zsh` установлен на машине во время `postinst`. При удалении пакета они удаляются через `postrm`.

```bash
cd cli/allta_cli
sh completions/install_completion.sh bash
sh completions/install_completion.sh zsh
```

Для POSIX `sh` автодополнение не поддерживается самим shell.

## Сервисные креды

```bash
allta creds
allta creds -s nexus
allta creds -a nexus -u admin -p secret
allta creds --add nexus --user admin -p secret
allta creds -up nexus -p new-secret
allta creds -up nexus -u admin2
allta creds -d nexus
allta creds nexus --raw
```

## VM команды

Для части VM-команд теперь можно передавать имена ВМ позиционно, без обязательного `--vms`.

```bash
allta vm start vm1 vm2
allta vm stop --vms vm1,vm2 vm3
allta vm astra-update --rc 1.8.1.6 vm1 vm2
allta vm snapshots vm1
allta vm snapshot-create --name snap1 vm1 vm2
allta vm snapshot-delete --name snap1 --vms vm1,vm2
allta vm snapshot-revert --name snap1 vm1
```

## Local VM команды

Local VM хранят inventory в `~/.config/allta/local_vm/`. Команды удаления чистят VM,
libvirt snapshot'ы, дисковые файлы VM и локальные файлы состояния (`vms.json`,
`snapshots.json`, `provider_vms_dates.json` и общий список snapshot'ов, если он есть).

```bash
allta local vm delete --all
allta local vm delete --vms vm1
allta local vm delete --vms vm1,vm2
allta local vm delete --vms vm1 --force
allta local vm delete --all --force
allta local vm clear
```

`delete --all` берёт список VM из `virsh list --all`, сверяет его с inventory и удаляет
только те VM, которые есть в обоих местах. Чужие VM без записи в inventory не трогает.
`delete --all --force` удаляет все VM из `virsh list --all`, включая VM без записи в
inventory, и всегда требует интерактивного подтверждения.

`delete --vms` удаляет только VM, которые есть в inventory. Если VM уже удалена с хоста
вручную, команда не падает, удаляет найденные дисковые файлы по inventory/типовому имени
и вычищает локальные записи. `--force` разрешает удалить указанную VM из libvirt даже без
записи в inventory.

`clear` сверяет inventory с libvirt и удаляет из файлов записи о VM, которых уже нет
на хосте.

## SSH команды

```bash
allta ssh <target>          # сервер / API VM / local VM
allta local ssh <vm-name>   # только local VM (без fallback на серверы)
```

## OS версии

```bash
allta server os-refresh        # ручное обновление списка ОС из внешнего API
allta server os-refresh --raw  # ответ API в JSON
```

## Сборка

Необходимо выполнять на версии ОС astra 1.7.5 для обеспечении наилучшей совместимости

```bash
cd cli
./build.sh allta_cli/
```

Далее полученный файл необходимо поместить на ftp сервер по пути: ftp://10.177.103.10/allta_1.0.0_amd64.deb
