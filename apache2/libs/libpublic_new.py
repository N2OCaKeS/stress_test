import json
from allta import PageBuilder, ConfluencePublisher

from apa_parse_results import build_tables_separately_for_each_metric
from apa_conf import ABP_VM_COUNT, ABP_VCPU, ABP_RAM, VM_KERNEL, VM_INFONAME

def apache2_publisher(
        username,
        token,
        space,
        parent_title,
        title,
        stand_number,
        total_rating,
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
        {"label": "VCPU", "value": ABP_VCPU},
        {"label": "RAM", "value": ABP_RAM},
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
    builder.add_heading(text=f"Total Rating: {total_rating}", level=2)
    
    builder.add_heading(text="Описание", level=2)
    builder.add_paragraph(
        text="Нагрузочное тестирование веб сервера Apache2 с PAM-аутентификацией \
            (Pluggable Authentication Modules — подключаемые модули аутентификации), посредством Apache Benchmark. \nТест разворачивает 2 ВМ (виртуальные машины): testvm1 — сервер, testvm2 — клиент."
    )
    builder.add_paragraph(text="Сравнивается производительность Apache2 в двух режимах: \
                          1. С PAM (AstraMode on): запросы выполняются от пользователей с разными метками МАС (мандатное управление доступом) — без категорий и с категориями. \
                          2. Без PAM (AstraMode off): запросы выполняются без аутентификации.")

    builder.add_heading(text="Результаты", level=2)
    
    df_rps, df_waiting = build_tables_separately_for_each_metric()
    df_rps_reset = df_rps.reset_index()
    df_rps_reset['concurrency_level'] = df_rps_reset['concurrency_level'].astype('Int64')
    dict_rps = {
        "title": "RPS",
        "headers": df_rps_reset.columns.tolist(),
        "rows": df_rps_reset.values.tolist()
    }

    df_waiting_reset = df_waiting.reset_index()
    df_waiting_reset['concurrency_level'] = df_waiting_reset['concurrency_level'].astype('Int64')
    dict_waiting = {
        "title": "Waiting (ms)",
        "headers": df_waiting_reset.columns.tolist(),
        "rows": df_waiting_reset.values.tolist()
    }

    builder.add_table(dict_rps)
    builder.add_table(dict_waiting)

    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
    )
    return builder, preview_path, publish_result
