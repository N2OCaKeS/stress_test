# Модуль allta

## Описание

Модуль содержит в себе реализацию классов упрощающих написание тестов для ОС Astra Linux

## Установка

```bash
pip install -i http://10.177.103.10:3141/root/release --trust 10.177.103.10 allta
```

## Документация

Документация доступна по следующей ссылке: <http://10.177.103.10:3141/root/release/allta/latest/+d/index.html>

## Сборка и публикация библиотеки

### Автоматически

1. В релизный репозиторий

    ```bash
    git check-out libs
    git merge --no-ff dev_libs --commit -m "allta_lib v1.2.3" # В версии указать актуальную версию библиотеки
    sleep 90
    echo "Библиотека успешно опубликована"
    ```

### Вручную

1. В релизный репозиторий

    ```bash
    cd libs/allta
    pip install -y -r req.txt
    devpi use http://10.177.103.10:3141/root/release
    devpi login <username> --password=<password>
    devpi upload --with-docs
    rm -rf allta.egg-info/ build/ dist/
    ```

2. В репозиторий разработки

    ```bash
    cd libs/allta
    pip install -y -r req.txt
    devpi use http://10.177.103.10:3141/user/dev
    devpi login <username> --password=<password>
    devpi upload --with-docs
    rm -rf allta.egg-info/ build/ dist/
    ```

3. В репозиторий отладки

    ```bash
    cd libs/allta
    pip install -y -r req.txt
    devpi use http://10.177.103.10:3141/debug/debug
    devpi login <username> --password=<password>
    devpi upload --with-docs
    rm -rf allta.egg-info/ build/ dist/
    ```

## Использование библиотеки

### Примеры

#### Развертывание ВМ и конфигурирование для теста

1. Развертывание ВМ и установка всех базовых зависимостей и настроек

    ```python
    from allta import Libvirt, LibvirtManager

    base_vms_dates = {
        "testvm1_server": {
            "ip_bridge": "10.177.103.158",
            "cpu": "4",
            "ram": "4096",
            "disk": "100",
            "additional_disks": {
                "disk1": {
                    "size": "100",  # default 10 gb
                    "mount_point": "/home/testuser2",  # default none, if default then not mount in vm
                    "fs_type": "ext4",  # default ext4
                },
                "disk2": {
                    "size": "100",  # default 10 gb
                    "mount_point": "/home/testuser2",  # default none, if default then not mount in vm
                    "fs_type": "ntfs",  # default ext4 FOR QCOW DISK
                },
                "disk3": {
                    "device": "/dev/vdb",  # default none
                    "fs_type": "ext4",  # default none FOR BLOCK DISK if default then NOT format
                    "mount_point": "/vms",  # default none, if default then not mount in vm
                },
                "disk4": {
                    "device": "/dev/vdc2",  # default none
                },
            },
        },
        "testvm2_client": {
            "cpu": "4",
            "ram": "4096",
            "disk": "50",            
        }
    }
    vms = list(vms_dates.keys())
    groups = {
        "all": vms
    }
    box = "1.8.1.o" # Используемый базовый образ
    rc = "1.8.5.45" # Необходимая версия Астры как в releases.json
    
    user_name = "u"
    password = "1"
    new_password = "12345"

    # Установка необходимых для сборки зависимостей
    Libvirt.prepare()

    # Сборка ВМ и получение ip адресов для ВМ без проброса в 103 подсеть
    actual_vms_dates = Libvirt.build(box=box, rc=rc, vms=vms, vms_dates=vms_dates, bridge=True)
    
    # Проверка доступности ВМ
    Libvirt.check(vms=vms, vms_dates=vms_dates)

    change_pass = {
        "g_all": {
            "prepare_task": {
                "command": f"yes {new_password} | sudo passwd {user_name}",
            }
        }
    }

    Libvirt.execute(commands=change_pass, vms_dates=actual_vms_dates, vms_groups = groups, username = user_name, password = password)

    scp_prepare = {
        "testvm1_server": [
            {"mode": "push", "path_host": "/test/path/prepare_server.sh", "path_vm": "/home/u/prepare.sh"}
        ],
        "testvm2_client": [
            {"mode": "push", "path_host": "/test/path/prepare_client.sh", "path_vm": "/home/u/prepare.sh"},
            {"mode": "push", "path_host": "/test/path/test.py.sh", "path_vm": "/home/u/test.py"}            
        ],

    }

    Libvirt.scp(scp_settings=scp_prepare, vms_dates=actual_vms_dates, vms_groups = groups, username = user_name, password = new_password)

    run_prepare = {
        "g_all": {
            "run_prepare": {
                "command": "sudo bash /home/u/prepare.sh",
                "signal set": "prepare",
            },
            "reboot": {
                "signal get": "prepare"
            }
        }
    }
    Libvirt.execute(commands=run_prepare, vms_dates=actual_vms_dates, vms_groups = groups, username = user_name, password = new_password)

    # Настройка /etc/hosts
    Libvirt.set_hosts(domain="test.domain", vms_dates=actual_vms_dates , username=user_name, password=new_password)

    run_test = {
        "testvm1_server": {
                "test_task": {
                    "command": "sudo perf record -g -a &",
                    "signal set": "server_start",
                    "nowait": True,  # default = False
                    "nowait_timeout": 3,  # default = 30 sec
                },
        },
        "testvm2_client": {
                "test_start_1_parametr": {
                    "command": "test.py 1",
                    "signal get": "server_start_1_param",
                    "signal set": "load_start_1",
                },
                "test_start_2_parametr": {
                    "command": "test.py 2",
                    "signal get": "load_start_1",
                    "signal set": "load_start_2",
                }, 
        }
    }

    get_result = {
        "testvm2_client": [
            {"mode": "pull", "path_host": "/test/path/result1.csv", "path_vm": "/home/u/result1.csv"},
            {"mode": "pull", "path_host": "/test/path/result2.csv", "path_vm": "/home/u/result2.csv"},       
        ]
    }
    LibvirtManager.Vm.stop(vms=vms)
    ```
