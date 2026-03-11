# from allta import PageBuilder, ConfluencePublisher Uncomment to work

import json
from allta import PageBuilder, ConfluencePublisher

from kernel_conf import VM_INFONAME, VM_KERNEL, SEGMENTATION_FAULT_RAM, SEGMENTATION_FAULT_VCPU, RESULTS_FILE

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

