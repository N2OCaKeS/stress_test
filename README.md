# Стандартные модули python для тестов

## Инструкция по работе с `astralinux_vm_controller`

### Описание
`astralinux_vm_controller` — это модуль для управления виртуальными машинами (ВМ) с использованием Vagrant и VirtualBox. Основной класс для работы — `VBox`, который реализует методы для подготовки, создания, проверки доступности и выполнения команд на ВМ.

### Установка
Перед началом работы убедитесь, что у вас установлены все зависимости, указанные в `setup.py` модуля `astralinux_vm_controller`. Для установки выполните:
```bash
pip install --extra-index-url http://10.177.5.61/user/dev/ astralinux_vm_controller
```

### Методы класса `VBox`

#### 1. `prepare(path_prepare: str) -> int`
Подготавливает окружение для работы с виртуальными машинами, выполняя указанный скрипт.

**Параметры:**
- `path_prepare` (str): Путь до скрипта подготовки.

**Пример параметра:**
```python
path_prepare = "/path/to/prepare_script.sh"
```

---

#### 2. `build(path_to_vagrantfile: str, box: str, rc: str, vms: list, vms_date: dict) -> int`
Создает и настраивает виртуальные машины на основе Vagrantfile.

**Параметры:**
- `path_to_vagrantfile` (str): Путь до Vagrantfile.
- `box` (str): Имя образа (бокса) для ВМ.
- `rc` (str): Версия операционной системы.
- `vms` (list): Список имен ВМ.
- `vms_date` (dict): Полная информация о ВМ.

**Пример параметров:**
```python
path_to_vagrantfile = "/path/to/Vagrantfile"
box = "astralinux/1.8"
rc = "1.8.1"
vms = ["database1", "database2"]
vms_date = {
    "database1": {
        "host-port": "2022",
        "ip": "192.168.56.101",
        "ip_bridge": "10.0.0.1",
        "cpus": "4",
        "memory": "4096"
    },
    "database2": {
        "host-port": "2023",
        "ip": "192.168.56.102",
        "ip_bridge": "10.0.0.2",
        "cpus": "4",
        "memory": "4096"
    }
}
```

---

#### 3. `check(vms: list, vm_dates: dict) -> int`
Проверяет доступность виртуальных машин через `ping`.

**Параметры:**
- `vms` (list): Список имен ВМ.
- `vm_dates` (dict): Полная информация о ВМ.

**Пример параметров:**
```python
vms = ["database1", "database2"]
vms_date = {
    "database1": {
        "host-port": "2022",
        "ip": "192.168.56.101",
        "ip_bridge": "10.0.0.1",
        "cpus": "4",
        "memory": "4096"
    },
    "database2": {
        "host-port": "2023",
        "ip": "192.168.56.102",
        "ip_bridge": "10.0.0.2",
        "cpus": "4",
        "memory": "4096"
    }
}
```

---

#### 4. `execute(vm_dates: dict, commands: dict, vms_groups: dict = None, username: str = "u", password: str = "1") -> int`
Выполняет команды на виртуальных машинах.

**Параметры:**
- `vm_dates` (dict): Полная информация о ВМ.
- `commands` (dict): Команды для выполнения.
- `vms_groups` (dict, optional): Группы ВМ.
- `username` (str, optional): Имя пользователя для SSH. По умолчанию `"u"`.
- `password` (str, optional): Пароль для SSH. По умолчанию `"1"`.

**Пример параметров:**
```python
commands = {
    "database1": {
        "install_nginx": {
            "command": "sudo apt-get install -y nginx",
            "signal set": "nginx_installed",
            "signal get": ""
        }
    },
    "g_databases": {
        "update_system": {
            "command": "sudo apt-get update",
            "signal set": "system_updated",
            "signal get": ""
        }
    }
}
vms_groups = {
    "databases": ["database1", "database2"]
}
```

---

#### 5. `apt.install(apt_structure: dict, vm_dates: dict, vms_groups: dict = None, username: str = "u", password: str = "1") -> int`
Устанавливает пакеты на виртуальных машинах.

**Параметры:**
- `apt_structure` (dict): Список пакетов для установки.
- `vm_dates` (dict): Полная информация о ВМ.
- `vms_groups` (dict, optional): Группы ВМ.
- `username` (str, optional): Имя пользователя для SSH. По умолчанию `"u"`.
- `password` (str, optional): Пароль для SSH. По умолчанию `"1"`.

**Пример параметров:**
```python
apt_structure = {
    "database1": ["nginx", "postgresql"],
    "g_databases": ["htop"]
}
```

---

#### 6. `scp.execute(scp: dict, vms_date: dict, groups: dict = None, username: str = "u", password: str = "1") -> dict`
Копирует файлы между локальной системой и ВМ.

**Параметры:**
- `scp` (dict): Настройки SCP.
- `vms_date` (dict): Полная информация о ВМ.
- `groups` (dict, optional): Группы ВМ.
- `username` (str, optional): Имя пользователя для SSH. По умолчанию `"u"`.
- `password` (str, optional): Пароль для SSH. По умолчанию `"1"`.

**Пример параметров:**
```python
scp_structure = {
    "database1": {
        "mode": "push", # Используется либо push (в ВМ) либо pull (из ВМ)
        "path_host": "/local/path/to/file",
        "path_vm": "/remote/path/to/file"
    }
}
```

---

#### 7. `hosts.set_hosts(vms_date: dict, domen: str, username: str = "u", password: str = "1")`
Настраивает файл `/etc/hosts` на виртуальных машинах.

**Параметры:**
- `vms_date` (dict): Полная информация о ВМ.
- `domen` (str): Домен для формирования FQDN.
- `username` (str, optional): Имя пользователя для SSH. По умолчанию `"u"`.
- `password` (str, optional): Пароль для SSH. По умолчанию `"1"`.

**Пример параметров:**
```python
domain = "example.com"
```

---

### Примеры использования

#### 1. Подготовка окружения
Метод `prepare` используется для выполнения скрипта подготовки окружения.
```python
from astralinux_vm_controller.vbox import VBox

# Путь до скрипта подготовки
path_prepare = "/path/to/prepare_script.sh"

# Выполнение подготовки
VBox.prepare(path_prepare)
```

#### 2. Создание виртуальных машин
Метод `build` используется для создания и настройки ВМ на основе Vagrantfile.
```python
# Параметры для создания ВМ
path_to_vagrantfile = "/path/to/Vagrantfile"
box = "astralinux/1.8"
rc = "1.8.1"
vms = ["database1", "database2"]
vms_date = {
    "database1": {
        "host-port": "2022",
        "ip": "192.168.56.101",
        "ip_bridge": "10.0.0.1",
        "cpus": "4",
        "memory": "4096"
    },
    "database2": {
        "host-port": "2023",
        "ip": "192.168.56.102",
        "ip_bridge": "10.0.0.2",
        "cpus": "4",
        "memory": "4096"
    }
}

# Создание ВМ
VBox.build(path_to_vagrantfile, box, rc, vms, vms_date)
```

#### 3. Проверка доступности ВМ
Метод `check` проверяет доступность ВМ через `ping`.
```python
# Проверка доступности ВМ
if VBox.check(vms, vms_date) == 0:
    print("Все ВМ доступны")
else:
    print("Некоторые ВМ недоступны")
```

#### 4. Выполнение команд на ВМ
Метод `execute` позволяет выполнять команды на ВМ.
```python
# Команды для выполнения
commands = {
    "database1": {
        "install_nginx": {
            "command": "sudo apt-get install -y nginx",
            "signal set": "nginx_installed"
        }
    },
    "g_databases": {
        "update_system": {
            "command": "sudo apt-get update",
            "signal set": "system_updated"
        }
    }
}

# Группы ВМ
vms_groups = {
    "databases": ["database1", "database2"]
}

# Выполнение команд
VBox.execute(vms_date, commands, vms_groups)
```

#### 5. Управление пакетами через `apt`
Класс `VBox` предоставляет методы для установки, удаления и переустановки пакетов.
```python
# Установка пакетов
apt_structure = {
    "database1": ["nginx", "postgresql"],
    "g_databases": ["htop"]
}

VBox.apt.install(apt_structure, vms_date, vms_groups)

# Удаление пакетов
VBox.apt.remove(apt_structure, vms_date, vms_groups)

# Переустановка пакетов
VBox.apt.reinstall(apt_structure, vms_date, vms_groups)
```

#### 6. Копирование файлов через `scp`
Метод `scp` используется для копирования файлов между локальной системой и ВМ.
```python
# Настройки SCP
scp_structure = {
    "database1": {
        "mode": "push",
        "path_host": "/local/path/to/file",
        "path_vm": "/remote/path/to/file"
    }
}

# Копирование файлов
VBox.scp.execute(scp_structure, vms_date, vms_groups)
```

#### 7. Настройка файла `/etc/hosts`
Метод `hosts` позволяет настроить файл `/etc/hosts` на ВМ.
```python
# Настройка /etc/hosts
domain = "example.com"
VBox.hosts.set_hosts(vms_date, domain)
```

### Заключение
Класс `VBox` предоставляет удобный интерфейс для управления виртуальными машинами. Используйте его методы для автоматизации задач, связанных с созданием, настройкой и управлением ВМ.