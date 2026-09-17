# from allta import PageBuilder, ConfluencePublisher Uncomment to work

import csv
import json
from pathlib import Path
from allta import PageBuilder, ConfluencePublisher

from kernel_conf import (
    VM_INFONAME,
    VM_KERNEL,
    SEGMENTATION_FAULT_RAM,
    SEGMENTATION_FAULT_VCPU,
    RESULTS_FILE,
    XFS_MEMORY_LEAK_RAM,
    XFS_MEMORY_LEAK_VCPU,
    USAGE_OS_IDLE_CSV,
    USAGE_OS_LOAD_CSV,
    USAGE_OS_MATH_MODEL_FILE,
    USAGE_OS_ALL_METRICS,
)

def kernel_publisher(
        username,
        token,
        space,
        parent_title,
        title,
        stand_number,
        lead_time="",
        test_cycle_version: str | None = None,
):
    preview_path = "report/confluence_report.html"
    reporter = ConfluencePublisher(
        base_url="https://life.astralinux.ru", username=username, token=token
    )
    builder = PageBuilder(title=title)

    with open(VM_INFONAME) as vm_info_av_file:
        vm_info_av = vm_info_av_file.read()
    with open(VM_KERNEL) as vm_info_kernel_file:
        vm_info_kernel = vm_info_kernel_file.read()

    params = [
        {"label": "VCPU", "value": SEGMENTATION_FAULT_VCPU},
        {"label": "RAM", "value": SEGMENTATION_FAULT_RAM},
    ]

    header_table = [
        {
            "label": "VM Astra Version",
            "value": vm_info_av
        },
        {
            "lavel": "VM Kernel",
            "value": vm_info_kernel
        },
        {
            "label": "Params",
            "value": {
                "items": params,
            },
        },
        {
            "label": "ARM",
            "value": {
                "stand_number": f"{stand_number}",
            },
        },
        {
            "label": "Lead time",
            "value": {
                "text": lead_time,
            },
        },
    ]

    builder.add_header_table(rows=header_table)
    builder.add_heading(text="Описание", level=2)
    builder.add_paragraph(
        text="Регрессионный тест на воспроизведение ошибки ядра Linux, при которой приложение может получить нулевые значения из корректно отображённых в память страниц файла.\nСбой проявляется при работе с mmap: вместо фактического содержимого страницы процесс читает нули. Если затронуты страницы с исполняемым кодом, это может приводить к аварийному завершению процесса."
    )
    builder.add_paragraph("Тест 1: Для воспроизведения базовой проблемы работы с отображаемыми \
                          файлами на подключенной файловой системе XFS подготавливается файл, \
                          содержащий 100 * 4096 байт соодинаковым значением 0x01: \
                          тест №1 производит отображение содержимого этого файла в память, \
                          а затем в цикле выполняет обращение к каждому 4-килобайтному блоку, проверяя, что из файла читается единица. \
                          После этого в параллельной сессии вызывается программа, \
                          которая создает нагрузку на подсистему памяти, \
                          путем запрашивания в цикле у системы память порциями по одному килобайту. \
                          При воспроизведении проблемы основная программа считывает с какой-то из страниц памяти некорректное значение.")

    builder.add_paragraph("Тест 2: Для воспроизведения проблемы с segmentation fault используется ещё одна тестовая программа, \
                          которая осуществляет вызов функций, \
                          расположенных на разных страницах памяти. \
                          В параллельной сессии также запускается программа, \
                          которая создает нагрузку на подсистему памяти.")

    with open(RESULTS_FILE, 'r') as f:
        results_dict = json.load(f)

    builder.add_table({
        "title": "Результаты тестирования",
        "headers": ["Наличие проблемы (Test 1)", "Наличие проблемы (Test 2)"],
        "rows": [[results_dict['status_test1'], results_dict['status_test2']]],
    })

    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
    )
    return builder, preview_path, publish_result

def xfs_memory_leak_publisher(username,
                              token,
                              space,
                              parent_title,
                              title,
                              stand_number,
                              lead_time="",
                              test_cycle_version: str | None = None,
):
    preview_path = "report/confluence_report.html"
    reporter = ConfluencePublisher(
        base_url="https://life.astralinux.ru", username=username, token=token
    )
    builder = PageBuilder(title=title)

    with open(VM_INFONAME) as vm_info_av_file:
        vm_info_av = vm_info_av_file.read()
    with open(VM_KERNEL) as vm_info_kernel_file:
        vm_info_kernel = vm_info_kernel_file.read()

    params = [
        {"label": "VCPU", "value": XFS_MEMORY_LEAK_VCPU},
        {"label": "RAM", "value": XFS_MEMORY_LEAK_RAM},
    ]

    header_table = [
        {
            "label": "VM Astra Version",
            "value": vm_info_av
        },
        {
            "lavel": "VM Kernel",
            "value": vm_info_kernel
        },
        {
            "label": "Params",
            "value": {
                "items": params,
            },
        },
        {
            "label": "ARM",
            "value": {
                "stand_number": f"{stand_number}",
            },
        },
        {
            "label": "Lead time",
            "value": {
                "text": lead_time,
            },
        },
    ]

    builder.add_header_table(rows=header_table)
    builder.add_heading(text="Описание", level=2)
    builder.add_paragraph(text="Регрессионный тест на воспроизведение ошибки ядра Linux, при которой при копировании файлов съедается USED память и не высвобождается.\n \
                          Проблема проявляется при работе с файловой системой XFS: при выполнении операций копирования файлов, связанных с XFS, наблюдается постепенное увеличение используемой памяти (USED), \
                          которая не освобождается после завершения операции. Это может привести к исчерпанию доступной памяти и снижению производительности системы.")


    with open(RESULTS_FILE, 'r') as f:
        results_dict = json.load(f)

    builder.add_table({
        "title": "Результаты тестирования",
        "headers": ["Утечка памяти"],
        "rows": [[results_dict['status']]],
    })

    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
    )
    return builder, preview_path, publish_result


def usage_os_publisher(username,
                              token,
                              space,
                              parent_title,
                              title,
                              stand_number,
                              lead_time="",
                              test_cycle_version: str | None = None,
):
    preview_path = "report/confluence_report.html"
    reporter = ConfluencePublisher(
        base_url="https://life.astralinux.ru", username=username, token=token
    )
    builder = PageBuilder(title=title)

    header_table = [
        {
            "label": "ARM",
            "value": {
                "stand_number": f"{stand_number}",
            },
        },
        {
            "label": "Lead time",
            "value": {
                "text": lead_time,
            },
        },
    ]

    builder.add_header_table(rows=header_table)
    builder.add_heading(text="Описание", level=2)
    builder.add_paragraph(
        text="Тест измеряет расход системных ресурсов ОС (CPU, RAM, переключения контекста, "
             "прерывания, load average) под синтетической нагрузкой (CPU/RAM/диск/сеть) в "
             "сравнении с состоянием простоя (idle). Учитываются только метрики ОС "
             "(вклад самих генераторов нагрузки из них вычтен, метрики хоста в рейтинг не входят). "
             "Итоговый рейтинг — взвешенное геометрическое среднее отношений idle/load по каждому "
             "показателю: 100 — деградации под нагрузкой нет, меньше 100 — есть деградация."
    )

    with open(USAGE_OS_MATH_MODEL_FILE, 'r') as f:
        results_dict = json.load(f)

    criteria = results_dict["criteria"]

    builder.add_heading(text="Результаты тестирования", level=2)
    builder.add_heading(text=f"Total Rating: {results_dict['rating']:.2f}", level=2)
    builder.add_table({
        "title": "Метрики (геометрическое среднее, load относительно idle-baseline)",
        "headers": ["Метрика", "Baseline (idle)", "Результат (load)", "Индекс"],
        "rows": [
            [
                crit["label"],
                f'{crit["baseline"]:.2f}',
                f'{crit["result"]:.2f}',
                f'{crit["ratio"] * 100:.2f}',
            ]
            for crit in criteria.values()
        ],
    })

    builder.add_attachment(file_path=USAGE_OS_IDLE_CSV, title="idle_os.csv")
    builder.add_attachment(file_path=USAGE_OS_LOAD_CSV, title="load_os.csv")
    builder.add_attachment(file_path=USAGE_OS_MATH_MODEL_FILE, title="math_model_results.json")

    with open(USAGE_OS_IDLE_CSV, newline='') as idle_file:
        idle_rows = list(csv.DictReader(idle_file))
    with open(USAGE_OS_LOAD_CSV, newline='') as load_file:
        load_rows = list(csv.DictReader(load_file))
    sample_count = min(len(idle_rows), len(load_rows))

    # 33 метрики на полном разрешении (600 точек) кладут тело страницы за лимит
    # Confluence REST (5 242 880 байт на запрос) — прореживаем до ~120 точек на график.
    CHART_MAX_POINTS = 120
    chart_step = max(1, sample_count // CHART_MAX_POINTS)
    sample_indices = range(0, sample_count, chart_step)

    graphics = [
        {
            "title": label,
            "type": "line",
            "x_key": "t",
            "series": ["idle", "load"],
            "series_colors": {
                "idle": "#36B37E",
                "load": "#FF5630",
            },
            "width": 500,
            "height": 300,
            "x_label": "Время, с",
            "y_label": label,
            "data": [
                {
                    "t": idle_rows[i]["elapsed_sec"],
                    "idle": float(idle_rows[i][column]),
                    "load": float(load_rows[i][column]),
                }
                for i in sample_indices
            ],
            "view_table": False,
        }
        for column, label in USAGE_OS_ALL_METRICS.items()
    ]
    builder.add_chart(chart_spec=graphics, columns=2)

    Path(preview_path).parent.mkdir(parents=True, exist_ok=True)
    builder.render_to_file(path=preview_path)

    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
    )
    return builder, preview_path, publish_result
