# from allta import PageBuilder, ConfluencePublisher Uncomment to work

import json
import pandas as pd
from allta import PageBuilder, ConfluencePublisher, MathModel

from net_conf import (
    IOF_RESULTS,
    VM_INFONAME,
    VM_KERNEL,
    KERNEL_NET_RAM,
    KERNEL_NET_VCPU,
    ITERATIONS,
    DHCP_VCPU,
    DHCP_RAM,
    DHCP_PERFDHCP_CLIENT_STEPS,
    DHCP_RESULTS,
)


def net_publisher(
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
        {"label": "VCPU", "value": KERNEL_NET_VCPU},
        {"label": "RAM", "value": KERNEL_NET_RAM},
        {"label": "Test iterations", "value": ITERATIONS},
    ]

    header_table = [
        {"label": "VM Astra Version", "value": vm_info_av},
        {"label": "VM Kernel", "value": vm_info_kernel},
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
        text="В тесте производится оценка сетевой производительности с помощью iperf при разных значениях параметра ядра init_on_free.\nСравниваются результаты в базовом состоянии (до изменения параметра) и после установки init_on_free=off."
    )

    with open(IOF_RESULTS, "r") as f:
        iof_results_dict = json.load(f)

    builder.add_table(
        {
            "title": "Результаты тестирования",
            "headers": [
                "init_on_free=on MBytes/sec (mean)",
                "init_on_free=off MBytes/sec (mean)",
                "Difference %",
            ],
            "rows": [
                [
                    iof_results_dict["init_on_free_ON"],
                    iof_results_dict["init_on_free_OFF"],
                    iof_results_dict["difference"],
                ]
            ],
        }
    )

    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
    )
    return builder, preview_path, publish_result


"""
    DHCP
"""


def build_dhcp_dataframe(results_path: str = DHCP_RESULTS) -> pd.DataFrame:
    with open(results_path) as f:
        data = json.load(f)

    rows = []
    for step in data.get("steps", []):
        perfdhcp = step.get("perfdhcp", {})
        discover_offer = perfdhcp.get("DISCOVER-OFFER", {})
        request_ack = perfdhcp.get("REQUEST-ACK", {})
        rate = step.get("perfdhcp_rate", {})
        kea_process = step.get("kea_process", {})
        kea_delta = step.get("kea_stats_delta", {})

        rows.append(
            {
                "clients_target": step.get("clients_target"),
                "rate_achieved": rate.get("achieved"),
                "rate_expected": rate.get("expected"),
                "do_sent": discover_offer.get("sent packets"),
                "do_received": discover_offer.get("received packets"),
                "do_drops_ratio": discover_offer.get("drops ratio"),
                "do_avg_delay_ms": discover_offer.get("avg delay"),
                "ra_sent": request_ack.get("sent packets"),
                "ra_received": request_ack.get("received packets"),
                "ra_drops_ratio": request_ack.get("drops ratio"),
                "ra_avg_delay_ms": request_ack.get("avg delay"),
                "kea_cpu_percent": kea_process.get("cpu_percent_avg"),
                "kea_rss_kb": kea_process.get("rss_kb_avg"),
                "kea_assigned_addresses": kea_delta.get("subnet[1].assigned-addresses"),
                "kea_allocation_fail": kea_delta.get("v4-allocation-fail", 0),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("clients_target").reset_index(drop=True)
        df["drops_ratio_avg_percent"] = (
            df["do_drops_ratio"].fillna(0.0) + df["ra_drops_ratio"].fillna(0.0)
        ) / 2
    return df


DHCP_RATING_BASE = 10000
DHCP_RATING_ZERO_FLOOR = 0.001
DHCP_RATING_CRITERIA = {
    "drops_ratio_avg_percent": 0.75,
    "do_avg_delay_ms": 0.25,
}
DHCP_REFERENCE_METRICS = {
    5000: {"drops_ratio_avg_percent": 0.01, "do_avg_delay_ms": 0.177},
    10000: {"drops_ratio_avg_percent": 0.005, "do_avg_delay_ms": 0.172},
    20000: {"drops_ratio_avg_percent": 0.0025, "do_avg_delay_ms": 0.196},
    40000: {"drops_ratio_avg_percent": 0.00125, "do_avg_delay_ms": 0.177},
    80000: {"drops_ratio_avg_percent": 0.000625, "do_avg_delay_ms": 0.175},
}


DHCP_RESULT_COLUMNS_DESCRIPTION = [
    {
        "parameter": "Клиенты",
        "description": "Количество синтетических DHCP-клиентов, заданное для шага нагрузки perfdhcp.",
    },
    {
        "parameter": "Rate achieved",
        "description": "Фактически достигнутая perfdhcp скорость генерации DHCP-запросов, запросов/сек.",
    },
    {
        "parameter": "Rate expected",
        "description": "Целевая скорость генерации DHCP-запросов, заданная параметром DHCP_PERFDHCP_RATE.",
    },
    {
        "parameter": "DISCOVER-OFFER drops %",
        "description": "Доля потерянных ответов на этапе DHCPDISCOVER -> DHCPOFFER.",
    },
    {
        "parameter": "REQUEST-ACK drops %",
        "description": "Доля потерянных ответов на этапе DHCPREQUEST -> DHCPACK.",
    },
    {
        "parameter": "Drops avg %",
        "description": "Средняя доля потерь между DISCOVER-OFFER и REQUEST-ACK; основной критерий total_rating.",
    },
    {
        "parameter": "Avg delay, мс",
        "description": "Средняя задержка ответа DHCP-сервера на этапе DISCOVER-OFFER; дополнительный критерий total_rating.",
    },
    {
        "parameter": "Kea CPU %",
        "description": "Средняя загрузка CPU процесса kea-dhcp4 во время шага нагрузки.",
    },
    {
        "parameter": "Kea RSS, KB",
        "description": "Средний RSS процесса kea-dhcp4 во время шага нагрузки, КБ.",
    },
]


def get_dhcp_total_rating(df: pd.DataFrame) -> int:
    if df.empty or len(df) < 2:
        raise ValueError("Нужно минимум 2 шага (строки) в df для расчёта рейтинга")

    df = df[df["clients_target"].isin(DHCP_REFERENCE_METRICS)].copy()
    if df.empty or len(df) < 2:
        raise ValueError("Нет данных для расчёта рейтинга по эталонным шагам DHCP")

    iterations = df["clients_target"].tolist()
    model = MathModel(type="ratio")

    for metric_name, weight in DHCP_RATING_CRITERIA.items():
        values = df[metric_name].fillna(0.0).tolist()
        reference = [
            DHCP_REFERENCE_METRICS[clients_target][metric_name]
            for clients_target in iterations
        ]
        model.add_criterion(
            name=metric_name,
            iterations=iterations,
            values=values,
            weight=weight,
            negative=True,
            reference=reference,
            zero_floor=DHCP_RATING_ZERO_FLOOR,
        )

    result = model.total_rating(scale=DHCP_RATING_BASE, cap=float("inf"))
    return round(result.total)


def update_dhcp_results_with_rating(results_path: str = DHCP_RESULTS) -> pd.DataFrame:
    df = build_dhcp_dataframe(results_path)
    rating = get_dhcp_total_rating(df)

    with open(results_path) as f:
        data = json.load(f)
    data["total_rating"] = rating
    with open(results_path, "w") as f:
        json.dump(data, f, ensure_ascii=False)

    return df


def dhcp_publisher(
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
        {"label": "VCPU", "value": DHCP_VCPU},
        {"label": "RAM", "value": DHCP_RAM},
        {"label": "Test iterations", "value": str(DHCP_PERFDHCP_CLIENT_STEPS)},
    ]

    criteria = [
        {
            "label": "drops_ratio_avg_percent",
            "value": "Средняя доля потерь, вес 0.75, меньше - лучше",
        },
        {
            "label": "do_avg_delay_ms",
            "value": "Средняя задержка DISCOVER-OFFER, вес 0.25, меньше - лучше",
        },
        {
            "label": "total_rating",
            "value": f"Взвешенное геометрическое среднее относительно эталона, база {DHCP_RATING_BASE}, zero_floor {DHCP_RATING_ZERO_FLOOR}",
        },
    ]

    header_table = [
        {"label": "VM Astra Version", "value": vm_info_av},
        {"label": "VM Kernel", "value": vm_info_kernel},
        {
            "label": "Params",
            "value": {
                "items": params,
            },
        },
        {
            "label": "Criteria",
            "value": {
                "items": criteria,
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
        text="В тесте производится оценка сетевой производительности DHCP-сервера kea с помощью perfdhcp"
    )


    df = update_dhcp_results_with_rating()

    with open(DHCP_RESULTS, "r") as f:
        dhcp_results_dict = json.load(f)

    total_rating = dhcp_results_dict["total_rating"]
    builder.add_heading(text=f"Total rating: {total_rating}", level=2)
    # builder.add_paragraph(str(dhcp_results_dict["total_rating"]))

    builder.add_table(
        {
            "title": "Описание параметров таблицы результатов",
            "headers": ["Параметр", "Описание"],
            "rows": [
                [item["parameter"], item["description"]]
                for item in DHCP_RESULT_COLUMNS_DESCRIPTION
            ],
        }
    )

    builder.add_table(
        {
            "title": "Результаты тестирования по шагам (N клиентов)",
            "headers": [
                "Клиенты",
                "Rate achieved",
                "Rate expected",
                "DISCOVER-OFFER drops %",
                "REQUEST-ACK drops %",
                "Drops avg %",
                "Avg delay, мс",
                "Kea CPU %",
                "Kea RSS, KB",
            ],
            "rows": df[
                [
                    "clients_target",
                    "rate_achieved",
                    "rate_expected",
                    "do_drops_ratio",
                    "ra_drops_ratio",
                    "drops_ratio_avg_percent",
                    "do_avg_delay_ms",
                    "kea_cpu_percent",
                    "kea_rss_kb",
                ]
            ].values.tolist(),
        }
    )

    builder.add_attachment(DHCP_RESULTS, title="dhcp_results.json")

    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
    )
    return builder, preview_path, publish_result


#### DHCP END
