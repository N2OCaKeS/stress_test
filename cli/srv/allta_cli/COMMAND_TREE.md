# allta CLI Command Tree

Source: `/home/n2ocake/git/stress_test/cli/srv/allta_cli/allta_cli/cli.py`

This file is auto-generated from Click decorators in `cli.py` and is meant for manual style review/edit planning.

## Global Shortcuts

- `lg` -> `login`
- `lo` -> `logout`
- `g` -> `git`
- `t` -> `tokens`
- `i` -> `ilo`
- `cr` -> `creds`
- `f` -> `file`
- `bx` -> `boxes`
- `rel` -> `releases`
- `py` -> `python`
- `srv` -> `server`
- `lc` -> `local`

## Root Sections

- `COMMANDS_NO_AUTH`: `login`, `boxes`, `releases`, `mc`, `local`, `python`, `venv`
- `COMMANDS_WITH_AUTH`: `logout`, `git`, `tokens`, `ilo`, `creds`, `file`, `ssh`, `server`, `vm`

## Command Tree

```text
allta
├─ `releases` - Показать версии releases.json.
├─ `boxes` - Показать имена боксов.
├─ `git` - Клонировать репозиторий.
├─ `tokens` (group) [default action] - Токены config-API.
│  ├─ `list` - Показать список токенов (details).
│  │  options:
│  │  - `--raw` - flag - Показать полный JSON.
│  ├─ `get` - Показать один токен по ключу.
│  │  args:
│  │  - `TOKEN_KEY`
│  │  options:
│  │  - `--raw` - flag - Показать полный JSON.
│  ├─ `set` - Создать или перезаписать токен.
│  │  args:
│  │  - `TOKEN_KEY`
│  │  options:
│  │  - `--token` - Значение токена.
│  │  - `--raw` - flag - Показать полный JSON.
│  ├─ `upd` - Обновить существующий токен.
│  │  args:
│  │  - `TOKEN_KEY`
│  │  options:
│  │  - `--token` - Новое значение токена.
│  │  - `--raw` - flag - Показать полный JSON.
│  └─ `del` - Удалить токен.
│     args:
│     - `TOKEN_KEY`
│     options:
│     - `--yes` - flag - Удалить без подтверждения.
├─ `ilo` - Получить iLO-креды.
│  args:
│  - `[STAND]` (optional)
│  options:
│  - `--raw` - flag - Показать полный JSON без попытки разбора.
├─ `creds` (group) [default action] - Сервисные логины и пароли.
│  options:
│  - `--raw` - flag - Показать полный JSON.
│  ├─ `list` - Показать все сервисные креды.
│  │  options:
│  │  - `--raw` - flag - Показать полный JSON.
│  ├─ `get` - Показать креды сервиса.
│  │  args:
│  │  - `SERVICE_NAME`
│  │  options:
│  │  - `--raw` - flag - Показать полный JSON.
│  ├─ `set` - Создать или перезаписать креды сервиса.
│  │  args:
│  │  - `SERVICE_NAME`
│  │  options:
│  │  - `-u/--user/--username` - required - metavar=USERNAME - Логин сервиса.
│  │  - `-p/--password` - Пароль сервиса.
│  │  - `--raw` - flag - Показать полный JSON.
│  ├─ `upd` - Обновить логин и/или пароль сервиса.
│  │  args:
│  │  - `SERVICE_NAME`
│  │  options:
│  │  - `-u/--user/--username` - metavar=USERNAME - Новый логин сервиса.
│  │  - `-p/--password` - Новый пароль сервиса.
│  │  - `--raw` - flag - Показать полный JSON.
│  └─ `del` - Удалить креды сервиса.
│     args:
│     - `SERVICE_NAME`
│     options:
│     - `--yes` - flag - Удалить без подтверждения.
├─ `file` - Скачать JSON-файл из config-API.
│  args:
│  - `FILENAME`
├─ `login` - Авторизоваться.
│  args:
│  - `[USERNAME]` (optional)
│  - `[PASSWORD]` (optional)
│  options:
│  - `-u/--user/--username/--usermane` - metavar=USERNAME - Логин (если не указан позиционно).
│  - `-p/--password` - metavar=PASSWORD - Пароль (если не указан позиционно). Небезопасно: будет удалено в будущих версиях.
│  - `--token` - flag - Войти по API token вместо пароля. Токен берётся из ALLTA_API_TOKEN/ALLTA_TOKEN или запрашивается интерактивно.
│  - `-g/--git` - flag - После входа: git clone.
│  - `-t/--tokens` - flag - После входа: получить tokens.json.
│  - `-k/--token-key` - metavar=KEY - После входа: получить конкретный ключ из tokens.json. Можно указать несколько раз.
│  - `-i/--ilo` - flag - После входа: показать все iLO credentials.
│  - `-s/--stand` - metavar=STAND - После входа: показать iLO только для указанного стенда.
│  - `-f/--file` - metavar=FILENAME - После входа: скачать JSON-файл из config-API. Можно указать несколько раз.
│  - `-b/--boxes` - flag - После входа: показать boxes.
│  - `-r/--releases` - flag - После входа: показать releases.
├─ `logout` - Выйти (revoke).
├─ `python` - Установить Python и venv.
│  options:
│  - `--venv` - flag - default=False - После установки сразу открыть shell с активированным venv.
├─ `venv` - Создать venv.
│  options:
│  - `--path` - metavar=DIR - Каталог для venv (должен существовать). По умолчанию: ./venv.
├─ `mc` (group) - Открыть MC на преднастроенных FTP.
│  ├─ `111` - MC на QA FTP.
│  ├─ `10` - MC на ftp://10.177.103.10/.
│  └─ `ci` - MC на CI FTP.
├─ `vm` (group) - Управление виртуальными машинами.
│  ├─ `list` - Показать все ВМ.
│  ├─ `create` - Создать ВМ (ip range=1).
│  │  options:
│  │  - `--server-id` - ID сервера.
│  │  - `--server` - Имя сервера или номер стенда (например, 12-my-host или 12).
│  │  - `--password` - required - Пароль для всех ВМ.
│  │  - `--vm` - required - metavar=NAME:IP:CPU:RAM - Описание ВМ. Можно указать несколько. Формат: name:ip:cpu:ram
│  ├─ `status` - Показать статус ВМ по имени.
│  │  args:
│  │  - `name`
│  │  options:
│  │  - `--json` - flag - Вывести ответ в JSON (как раньше).
│  ├─ `status-set` - Поставить статус равным вашему логину.
│  │  args:
│  │  - `name`
│  ├─ `status-free` - Освободить ВМ (сделать свободной).
│  │  args:
│  │  - `name`
│  ├─ `start` - Старт ВМ по именам.
│  │  args:
│  │  - `[VM]...`
│  │  options:
│  │  - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
│  ├─ `stop` - Стоп ВМ по именам.
│  │  args:
│  │  - `[VM]...`
│  │  options:
│  │  - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
│  ├─ `astra-update` - Astra update для выбранных ВМ.
│  │  args:
│  │  - `[VM]...`
│  │  options:
│  │  - `--rc` - required - Версия rc, напр. 1.8.1.6
│  │  - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
│  ├─ `snapshots` - Список снимков по имени ВМ.
│  │  args:
│  │  - `[VM]` (optional)
│  │  options:
│  │  - `--vm` - Имя ВМ
│  ├─ `snapshot-create` - Создать снимок для ВМ (по именам).
│  │  args:
│  │  - `[VM]...`
│  │  options:
│  │  - `--name` - required - Имя снимка.
│  │  - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
│  ├─ `snapshot-delete` - Удалить снимок по именам ВМ.
│  │  args:
│  │  - `[VM]...`
│  │  options:
│  │  - `--name` - required - Имя снимка.
│  │  - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
│  └─ `snapshot-revert` - Откатить ВМ к снимку (по именам).
│     args:
│     - `[VM]...`
│     options:
│     - `--name` - required - Имя снимка.
│     - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
├─ `local` (group) - Локальные команды без авторизации.
│  ├─ `ssh` - Подключиться по SSH только к local VM.
│  │  args:
│  │  - `VM`
│  └─ `vm` (group) - Локальные VM через allta_lib.
│     ├─ `build` - Собрать локальные ВМ через allta_lib.
│     │  args:
│     │  - `[BASENAME]` (optional)
│     │  options:
│     │  - `--name` - Базовое имя ВМ (по умолчанию testvm1).
│     │  - `--count` - default=1 - Количество ВМ (минимум 1).
│     │  - `--cpu` - default=4 - CPU на ВМ (минимум 1).
│     │  - `--ram` - default=4 - RAM на ВМ (GB, минимум 2).
│     │  - `--rc` - required - Версия RC.
│     │  - `--disk` - default=20 - Размер диска (минимум 11).
│     │  - `--box` - Имя box для allta_lib. Если не задано: автоподбор по rc.
│     ├─ `b` [hidden]
│     │  args:
│     │  - `[BASENAME]` (optional)
│     │  options:
│     │  - `--name` - Базовое имя ВМ (по умолчанию testvm1).
│     │  - `--count` - default=1 - Количество ВМ (минимум 1).
│     │  - `--cpu` - default=4 - CPU на ВМ (минимум 1).
│     │  - `--ram` - default=4 - RAM на ВМ (GB, минимум 2).
│     │  - `--rc` - required - Версия RC.
│     │  - `--disk` - default=20 - Размер диска (минимум 11).
│     │  - `--box` - Имя box для allta_lib. Если не задано: автоподбор по rc.
│     ├─ `list` - Показать локальные ВМ.
│     ├─ `status` - Показать статус локальной ВМ.
│     │  args:
│     │  - `name`
│     │  options:
│     │  - `--json` - flag - Вывести ответ в JSON.
│     ├─ `start` - Старт локальных ВМ по именам.
│     │  args:
│     │  - `[VM]...`
│     │  options:
│     │  - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
│     ├─ `stop` - Стоп локальных ВМ по именам.
│     │  args:
│     │  - `[VM]...`
│     │  options:
│     │  - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
│     ├─ `astra-update` - Astra update для local VM.
│     │  args:
│     │  - `[VM]...`
│     │  options:
│     │  - `--rc` - required - Версия rc, напр. 1.8.1.6
│     │  - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
│     ├─ `snapshots` - Список снимков local VM.
│     │  args:
│     │  - `[VM]` (optional)
│     │  options:
│     │  - `--vm` - Имя ВМ
│     ├─ `snapshot-create` - Создать snapshot для local VM.
│     │  args:
│     │  - `[VM]...`
│     │  options:
│     │  - `--name` - required - Имя снимка.
│     │  - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
│     ├─ `snapshot-delete` - Удалить snapshot для local VM.
│     │  args:
│     │  - `[VM]...`
│     │  options:
│     │  - `--name` - required - Имя снимка.
│     │  - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
│     └─ `snapshot-revert` - Откатить local VM к snapshot.
│        args:
│        - `[VM]...`
│        options:
│        - `--name` - required - Имя снимка.
│        - `--vms` - metavar=VM[,VM2,...] - Имена ВМ. Можно через запятую и вместе с позиционными.
├─ `server` (group) - Управление физическими серверами.
│  ├─ `password` (group) - CRUD паролей для снимков/ОС.
│  │  ├─ `list` - Показать все пароли версий ОС.
│  │  │  options:
│  │  │  - `--raw` - flag - Вывести ответ в JSON.
│  │  ├─ `get` - Показать пароль по имени версии ОС.
│  │  │  args:
│  │  │  - `OS_VERSION`
│  │  │  options:
│  │  │  - `--raw` - flag - Вывести ответ в JSON.
│  │  ├─ `set` - Создать или обновить пароль для версии ОС.
│  │  │  args:
│  │  │  - `OS_VERSION`
│  │  │  options:
│  │  │  - `--password` - Пароль для версии ОС.
│  │  │  - `--ssh-user` - SSH пользователь версии ОС.
│  │  │  - `--raw` - flag - Вывести ответ в JSON.
│  │  ├─ `upd` - Обновить существующий пароль версии ОС.
│  │  │  args:
│  │  │  - `OS_VERSION`
│  │  │  options:
│  │  │  - `--password` - Новый пароль.
│  │  │  - `--ssh-user` - Новый SSH пользователь.
│  │  │  - `--raw` - flag - Вывести ответ в JSON.
│  │  └─ `del` - Удалить пароль версии ОС.
│  │     args:
│  │     - `OS_VERSION`
│  │     options:
│  │     - `--yes` - flag - Удалить без подтверждения.
│  ├─ `list` - Показать все серверы.
│  │  options:
│  │  - `--raw` - flag - Вывести ответ в JSON.
│  ├─ `show` - Показать один сервер по имени или номеру стенда.
│  │  args:
│  │  - `SERVER`
│  │  options:
│  │  - `--raw` - flag - Вывести ответ в JSON.
│  ├─ `add` - Добавить сервер.
│  │  options:
│  │  - `--name` - required - Имя сервера в формате N-name.
│  │  - `--ip` - required - IP сервера.
│  │  - `--cpu` - required - Количество CPU.
│  │  - `--ram` - required - Количество RAM.
│  │  - `--phy-if` - default=eth0 - Физический интерфейс.
│  │  - `--virt/--no-virt` - default=False - Флаг виртуализации.
│  │  - `--ssh-port` - default=22 - SSH порт.
│  │  - `--server-user` - required - Пользователь ОС сервера.
│  │  - `--server-password` - Пароль ОС сервера.
│  │  - `--driver-type` - default=ilo - Тип BMC.
│  │  - `--ipmi-ip` - required - IP IPMI/iLO.
│  │  - `--ipmi-user` - required - Пользователь IPMI/iLO.
│  │  - `--ipmi-password` - Пароль IPMI/iLO.
│  │  - `--os-version-id` - ID версии ОС.
│  ├─ `upd` - Обновить сервер.
│  │  args:
│  │  - `[SERVER]` (optional)
│  │  options:
│  │  - `--all` - flag - Обновить указанные поля сразу на всех серверах.
│  │  - `--name` - Новое имя.
│  │  - `--ip` - Новый IP.
│  │  - `--cpu` - Новое количество CPU.
│  │  - `--ram` - Новое количество RAM.
│  │  - `--phy-if` - Новый физический интерфейс.
│  │  - `--virt` - Включить виртуализацию.
│  │  - `--no-virt` - Выключить виртуализацию.
│  │  - `--ssh-port` - Новый SSH порт.
│  │  - `--server-user` - Новый пользователь ОС.
│  │  - `--driver-type` - Новый тип BMC.
│  │  - `--ipmi-ip` - Новый IP IPMI/iLO.
│  │  - `--ipmi-user` - Новый пользователь IPMI/iLO.
│  │  - `--status` - Новый статус.
│  │  - `--os-version-id` - Новый ID версии ОС.
│  ├─ `del` - Удалить сервер.
│  │  args:
│  │  - `SERVER`
│  │  options:
│  │  - `--yes` - flag - Удалить без подтверждения.
│  ├─ `passwd` - Сменить пароль сервера или IPMI/iLO.
│  │  args:
│  │  - `TARGET`
│  │  - `[SERVER_OR_PASSWORD]` (optional)
│  │  - `[PASSWORD]` (optional)
│  │  options:
│  │  - `--all` - flag - Сменить пароль на всех серверах.
│  ├─ `start` - Включить сервер через server API/IPMI.
│  │  args:
│  │  - `SERVER`
│  ├─ `stop` - Выключить сервер через server API/IPMI.
│  │  args:
│  │  - `SERVER`
│  ├─ `reboot` - Перезагрузить сервер через server API/IPMI.
│  │  args:
│  │  - `SERVER`
│  ├─ `os-refresh` - Обновить список версий ОС из внешнего API.
│  │  options:
│  │  - `--raw` - flag - Вывести ответ в JSON.
│  ├─ `on` [hidden] - Алиас для start.
│  │  args:
│  │  - `SERVER`
│  ├─ `off` [hidden] - Алиас для stop.
│  │  args:
│  │  - `SERVER`
│  └─ `release` - Освободить сервер.
│     args:
│     - `SERVER`
└─ `ssh` - Подключиться по SSH к серверу или ВМ.
   args:
   - `TARGET`
```

## Notes

- `tokens` and `creds` support legacy positional compatibility via custom `resolve_command` (e.g. `allta tokens <KEY>`, `allta creds <SERVICE>`).
- `server` has alias resolution `passwords -> password` via custom `get_command`.
