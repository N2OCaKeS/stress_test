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
