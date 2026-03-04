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
    from pathlib import Path
    from allta import ConfluencePublisher, Libvirt, LibvirtManager, PageBuilder
    from pathlib import Path

    vms_dates = {
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

    vms_data_path = "vms_dates.txt"

    if Path("vms_data_path").is_file():
        # Загружаем сохраненные данные о ВМ
        vms_dates = LibvirtManager.Vm.load_vms_data(save_path=vms_data_path)

        # Откатываем снимок для ВМ
        LibvirtManager.Snapshot.revert(vms=vms, snapshot_name="prepare")

        # Ждем включения
        sleep(90)

        # Проверка доступности ВМ
        Libvirt.check(vms=vms, vms_dates=actual_vms_dates)

    else:
        # Установка необходимых для сборки зависимостей
        Libvirt.prepare()

        # Сборка ВМ и получение ip адресов для ВМ без проброса в 103 подсеть
        actual_vms_dates = Libvirt.build(box=box, rc=rc, vms=vms, vms_dates=vms_dates, bridge=True)
        
        # Проверка доступности ВМ
        Libvirt.check(vms=vms, vms_dates=actual_vms_dates)

        change_pass = {
            "g_all": {
                "prepare_task": {
                    "command": f"yes {new_password} | sudo passwd {user_name}",
                }
            }
        }
        # Выполнение команды на ВМ
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
        # Отправка файлов на ВМ
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
        # Выполнение команды на ВМ
        Libvirt.execute(commands=run_prepare, vms_dates=actual_vms_dates, vms_groups = groups, username = user_name, password = new_password)

        # Создание снимка ВМ после сборки
        LibvirtManager.Snapshot.create(vms=vms, snapshot_name="prepare")

        # Сохранение данных о ВМ для перезапусков теста без новой сборки ВМ и сокращения времени на отладку
        LibvirtManager.Vm.save_vms_data(vms_dates=vms_dates, save_path=vms_data_path)

    # Настройка /etc/hosts
    Libvirt.set_hosts(domain="test.domain", vms_dates=actual_vms_dates , username=user_name, password=new_password)

    run_test = {
        "testvm1_server": {
                "test_task": {
                    "command": "sudo perf record -g -a",
                    "signal set": "server_start",
                    "nowait": True,  # default = False
                    "nowait_mode": "terminate",  # terminate|continue, default = terminate
                    "nowait_timeout": 3,  # default = 30 sec
                },
        },
        "testvm2_client": {
                "test_start_1_parametr": {
                    "command": "test.py 1",
                    "signal get": "server_start",  # в nowait сигнал ставится через +10 секунд от старта команды
                    "signal set": "load_start_1",
                },
                "test_start_2_parametr": {
                    "command": "test.py 2",
                    "signal get": "load_start_1",
                    "signal set": "load_start_2",
                }, 
        }
    }

    # Отдельный пример для nowait_mode=continue:
    # сервис запускается в фоне и не зависит от закрытия SSH-сессии
    run_background = {
        "testvm1_server": {
                "iperf_server": {
                    "command": "iperf -s",
                    "signal set": "iperf_server_started",
                    "nowait": True,
                    "nowait_mode": "continue",
                },
        },
        "testvm2_client": {
                "load_test": {
                    "command": "iperf -c testvm1_server -t 60",
                    "signal get": "iperf_server_started",
                }
        }
    }
    Libvirt.execute(commands=run_background, vms_dates=actual_vms_dates, vms_groups=groups, username=user_name, password=new_password)

    get_result = {
        "testvm2_client": [
            {"mode": "pull", "path_host": "/test/path/result1.csv", "path_vm": "/home/u/result1.csv"},
            {"mode": "pull", "path_host": "/test/path/result2.csv", "path_vm": "/home/u/result2.csv"},       
        ]
    }
    # Получение файлов с ВМ
    Libvirt.scp(scp_settings=get_result, vms_dates=actual_vms_dates, vms_groups=groups, username=user_name, password=new_password)

    # Выключение ВМ
    LibvirtManager.Vm.stop(vms=vms)

    LibvirtManager.Vm.stop(vms=vms)

    # Формирование и публикация отчета в Confluence
    report_dir = Path("/test/path")
    result_file_1 = report_dir / "result1.csv"
    result_file_2 = report_dir / "result2.csv"
    cpu_chart = report_dir / f"cpu_{rc}.png"

    builder = PageBuilder(title=f"Stress report {rc}")
    builder.add_heading("Сводка", level=2)
    builder.add_header_table([
        {"label": "Версия", "value": rc},
        {"label": "ARM", "value": {"stand_number": "12"}},
        {"label": "ВМ", "value": {"items": vms}},
        {"label": "Lead time", "value": "00:10:42"},
    ])
    builder.add_table({
        "title": "Результаты",
        "headers": ["Метрика", "Значение"],
        "rows": [
            {"Метрика": "Успешных сценариев", "Значение": "42"},
            {"Метрика": "Ошибок", "Значение": "0"},
        ],
    })
    builder.add_gallery(
        [{"title": "CPU profile", "attachment": cpu_chart, "src": cpu_chart.name}],
        columns=1,
    )
    builder.add_attachment(result_file_1, display=False)
    builder.add_attachment(result_file_2, display=False)

    publisher = ConfluencePublisher(
        base_url="confluence.company.local",
        username="ci-bot",
        token="<confluence_token>",
    )
    publish_result = publisher.publish_results_from_params(
        conf_space="STRESS",
        conf_parent_page="Системные службы",
        conf_new_page_name=f"test_report_{rc}_stand12",
        test_cycle_version=rc,
        body=builder,
        attachments=builder.attachments,
        create_tree=True,  # global -> detailed -> more
    )
    print(publish_result)  # {"page_id": "...", "release_page_id": "..."}
    ```

#### Формирование и публикация отчета в Confluence

1. Сформируйте содержимое страницы через `PageBuilder`.
   Можно использовать все публичные методы конструктора:
   `add_raw_html`, `add_heading`, `add_paragraph`, `add_unordered_list`,
   `add_ordered_list`, `add_table`, `add_header_table`, `add_details_table`,
   `add_attachment`, `add_gallery`, `add_chart`, `render`, `render_to_file`,
   `attachments`.
2. Добавьте вложения через `add_attachment(...)` или передайте каталог файлов в `attachments_dir`.
3. Создайте клиент `ConfluencePublisher` с `base_url`, `username` и `token` (или `password`).
4. Вызовите `publish_results_from_params(...)`:
   - `create_tree=True` публикует отчет в дерево версий (`global -> detailed -> more`) и возвращает `page_id` + `release_page_id`.
   - если задан `conf_parent_page`, под каждой версией создаётся контейнер `STRESS_report <version> ⬝ <conf_parent_page>` с макросом `children`, а сам отчет публикуется дочерней страницей `conf_new_page_name`.
   - `create_tree=False` публикует только одну страницу под `conf_parent_page` (или в корень space).
5. Передавайте в `body` именно объект `PageBuilder` (метод сам вызовет `render()` внутри).

Максимально подробный пример (используются все публичные методы `PageBuilder`):

```python
from pathlib import Path
from allta import ConfluencePublisher, PageBuilder

test_cycle_version = "1.8.4.46"
report_dir = Path("/tmp/report")
artifacts_dir = report_dir / "artifacts"
image_cpu = report_dir / "cpu_usage.png"
image_mem = report_dir / "memory_usage.png"
summary_csv = report_dir / f"summary_{test_cycle_version}.csv"
raw_zip = report_dir / f"raw_data_{test_cycle_version}.zip"
report_dir.mkdir(parents=True, exist_ok=True)
artifacts_dir.mkdir(parents=True, exist_ok=True)

builder = PageBuilder(title=f"Stress report {test_cycle_version}")

# add_raw_html
builder.add_raw_html(
    "<ac:structured-macro ac:name=\"info\">"
    "<ac:rich-text-body><p>"
    "Страница сформирована автоматически библиотекой allta."
    "</p></ac:rich-text-body>"
    "</ac:structured-macro>"
)

# add_heading + add_paragraph
builder.add_heading("Общая информация", level=2)
builder.add_paragraph(
    "Отчет по нагрузочному тестированию.\n"
    f"Версия тестового цикла: {test_cycle_version}.\n"
    "Среда: stand12."
)

# add_unordered_list
builder.add_unordered_list([
    "Сценарий: apache-rp",
    "Пользователей: 3000",
    "Ошибок на уровне приложения: 0",
])

# add_ordered_list
builder.add_ordered_list([
    "Развернуть окружение",
    "Выполнить нагрузку",
    "Собрать и опубликовать артефакты",
])

# add_header_table
builder.add_header_table([
    {"label": "Версия", "value": test_cycle_version},
    {"label": "ARM", "value": {"stand_number": "12"}},
    {"label": "Params", "value": {"items": ["users=3000", "duration=600s"]}},
    {"label": "Lead time", "value": "00:10:42"},
])

# add_details_table (алиас add_header_table)
builder.add_details_table([
    {"label": "Owner", "value": "qa-team"},
    {"label": "Build", "value": {"text": "CI #4242", "link": "https://ci.example/job/4242"}},
    {"label": "Notes", "value": ["all checks passed", "no critical alerts"]},
])

# add_table
builder.add_table({
    "title": "Итоговые метрики",
    "title_level": 3,
    "description": "Сводные значения по запуску",
    "headers": ["Метрика", "Значение", "Ед."],
    "rows": [
        {"Метрика": "TPS", "Значение": 1580, "Ед.": "req/s"},
        {"Метрика": "Latency p95", "Значение": 120, "Ед.": "ms"},
        {"Метрика": "Errors", "Значение": 0, "Ед.": "%"},
    ],
})

# add_chart (один график)
builder.add_chart({
    "title": "Throughput / Latency",
    "type": "line",
    "x_key": "time",
    "series": ["tps", "latency_p95"],
    "x_label": "Time",
    "x_unit": "s",
    "y_label": "Value",
    "colors": ["#0052CC", "#FF5630"],
    "view_table": True,
    "data": [
        {"time": 0, "tps": 1000, "latency_p95": 80},
        {"time": 30, "tps": 1200, "latency_p95": 90},
        {"time": 60, "tps": 1580, "latency_p95": 120},
    ],
})

# add_chart (несколько графиков в сетке)
builder.add_chart(
    [
        {
            "title": "CPU %",
            "type": "area",
            "x_key": "time",
            "series": ["cpu"],
            "data": [{"time": 0, "cpu": 45}, {"time": 30, "cpu": 72}, {"time": 60, "cpu": 68}],
        },
        {
            "title": "Memory %",
            "type": "column",
            "x_key": "time",
            "series": ["memory"],
            "data": [{"time": 0, "memory": 41}, {"time": 30, "memory": 57}, {"time": 60, "memory": 63}],
        },
    ],
    columns=2,
)

# add_gallery
builder.add_gallery(
    [
        {
            "title": "CPU usage",
            "src": image_cpu.name,
            "attachment": image_cpu,
            "caption": "Профиль загрузки CPU",
        },
        {
            "title": "Memory usage",
            "src": image_mem.name,
            "attachment": image_mem,
            "description": "Использование RAM во времени",
        },
    ],
    columns=2,
)

# add_attachment (видимые блоки на странице)
builder.add_attachment(summary_csv, title="Summary CSV", description="Сводные метрики", display=True)
builder.add_attachment(raw_zip, title="Raw data archive", description="Полный набор артефактов", display=True)

# add_attachment (скрытая регистрация вложений)
builder.add_attachment(report_dir / "result1.csv", display=False)
builder.add_attachment(report_dir / "result2.csv", display=False)

# render + render_to_file
html_preview = builder.render()
preview_path = builder.render_to_file(report_dir / "preview_report.html")
print(f"HTML size: {len(html_preview)}")
print(f"Preview saved to: {preview_path}")

publisher = ConfluencePublisher(
    base_url="confluence.company.local",
    username="ci-bot",
    token="<confluence_token>",
    publish_retry_interval=180,
    publish_retry_timeout=1800,
)
result = publisher.publish_results_from_params(
    conf_space="STRESS",
    conf_parent_page="Системные службы",
    conf_new_page_name=f"apache-rp_{test_cycle_version}_smolensk_stand12",
    test_cycle_version=test_cycle_version,
    body=builder,
    attachments=builder.attachments,  # @property attachments
    attachments_dir=artifacts_dir,  # дополнительные файлы из каталога
    create_tree=True,
)
print(result)
```
