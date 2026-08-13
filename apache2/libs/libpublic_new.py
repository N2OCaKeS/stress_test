import json
import re
from allta import PageBuilder, ConfluencePublisher

from apa_parse_results import build_tables_separately_for_each_metric
from apa_conf import (
    ABP_VM_COUNT,
    ABP_VCPU,
    ABP_RAM,
    A_BALANCE_BACKEND_COUNT,
    A_BALANCE_CLIENT_COUNT,
    A_BALANCE_KEEPALIVED_VRID,
    A_BALANCE_LB_COUNT,
    A_BALANCE_RAM,
    A_BALANCE_VCPU,
    A_BALANCE_VIP,
    A_BALANCE_VM_COUNT,
    BALANCE_RESULTS,
    VM_INFONAME,
    VM_KERNEL,
)

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
            "label": "VM Kernel",
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

    df_waiting_reset = df_waiting.reset_index().astype('Int64')
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


def apache_balance_publisher(
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
        {"label": "VCPU", "value": A_BALANCE_VCPU},
        {"label": "RAM", "value": A_BALANCE_RAM},
        {"label": "VM count", "value": A_BALANCE_VM_COUNT},
        {"label": "Topology", "value": f"{A_BALANCE_LB_COUNT} LB + VIP + {A_BALANCE_BACKEND_COUNT} backend + {A_BALANCE_CLIENT_COUNT} client"},
    ]

    header_table = [
        {
            "label": "VM Astra Version",
            "value": vm_info_av
        },
        {
            "label": "VM Kernel",
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
        text="Нагрузочное тестирование Apache2 в режиме балансировки встроенными "
             "средствами Apache, посредством Apache Benchmark. Тест разворачивает "
             "5 ВМ: testvm1 и testvm2 — Apache LB с keepalived VIP, "
             "testvm3 и testvm4 — backend Apache, testvm5 — ab client."
    )
    builder.add_paragraph(
        text=f"Ступенчатая нагрузка с нарастающим concurrency подается клиентом на VIP {A_BALANCE_VIP}. "
             "В расчет берутся Requests per second и медиана Waiting из общего summary_balance.txt."
    )

    builder.add_heading(text="Результаты", level=2)

    records = []
    with open(BALANCE_RESULTS, encoding="utf-8") as result_file:
        content = result_file.read()
    for block in re.split(r"This is ApacheBench", content):
        concurrency = re.search(r"Concurrency Level:\s+(\d+)", block)
        rps = re.search(r"Requests per second:\s+([\d.]+)", block)
        waiting = re.search(
            r"Waiting:\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
            block,
        )
        if not all([concurrency, rps, waiting]):
            continue
        records.append([
            int(concurrency.group(1)),
            float(rps.group(1)),
            float(waiting.group(4)),
        ])

    dict_metrics = {
        "title": "Apache balance metrics",
        "headers": ["concurrency_level", "requests_per_second", "waiting_median_ms"],
        "rows": records,
    }

    builder.add_table(dict_metrics)

    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
    )
    return builder, preview_path, publish_result
