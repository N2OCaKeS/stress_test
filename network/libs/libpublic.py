# from allta import PageBuilder, ConfluencePublisher Uncomment to work

import json
import math
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
        # Если perfdhcp не отдал одну из секций, среднее остаётся NaN, а не занижается до 0
        df["drops_ratio_avg_percent"] = (df["do_drops_ratio"] + df["ra_drops_ratio"]) / 2
    return df


DHCP_RATING_POWER = 0.9998061238066913

DHCP_RATING_SCALE = 100000000


# (колонка df, заголовок в отчёте, описание)
DHCP_RESULT_COLUMNS = [
    (
        "clients_target",
        "Клиенты",
        "Количество синтетических DHCP-клиентов, заданное для шага нагрузки perfdhcp.",
    ),
    (
        "rate_achieved",
        "Достигнутая скорость, запр/с",
        "Фактически достигнутая perfdhcp скорость генерации DHCP-запросов.",
    ),
    (
        "rate_expected",
        "Целевая скорость, запр/с",
        "Целевая скорость генерации DHCP-запросов, заданная параметром DHCP_PERFDHCP_RATE.",
    ),
    (
        "do_drops_ratio",
        "Потери DISCOVER-OFFER, %",
        "Доля потерянных ответов на этапе DHCPDISCOVER -> DHCPOFFER.",
    ),
    (
        "ra_drops_ratio",
        "Потери REQUEST-ACK, %",
        "Доля потерянных ответов на этапе DHCPREQUEST -> DHCPACK.",
    ),
    (
        "drops_ratio_avg_percent",
        "Средние потери, %",
        "Среднее потерь DISCOVER-OFFER и REQUEST-ACK; основной критерий рейтинга (вес 0.75).",
    ),
    (
        "do_avg_delay_ms",
        "Средняя задержка, мс",
        "Средняя задержка ответа DHCP-сервера на этапе DISCOVER-OFFER; дополнительный критерий рейтинга (вес 0.25).",
    ),
    (
        "kea_cpu_percent",
        "Загрузка CPU kea, %",
        "Средняя загрузка CPU процесса kea-dhcp4 во время шага нагрузки.",
    ),
    (
        "kea_rss_kb",
        "Память kea (RSS), КБ",
        "Средний объём резидентной памяти (RSS) процесса kea-dhcp4 во время шага нагрузки.",
    ),
]


# критерий -> (вес, границы min-max нормализации)
DHCP_RATING_CRITERIA = {
    "drops_ratio_avg_percent": (0.75, (0.0, 100.0)),
    "do_avg_delay_ms": (0.25, (0.0, 10.0)),
}
# MathModel выкидывает критерий, если все его значения <= 1e-6, поэтому прогон
# с нулевыми потерями получал бы рейтинг ниже любого прогона с потерями
DHCP_RATING_MIN_VALUE = 1e-5


def get_dhcp_total_rating(df: pd.DataFrame, power: float = DHCP_RATING_POWER) -> int:
    if df.empty:
        raise ValueError("Нет шагов в результатах DHCP для расчёта рейтинга")

    df = (
        df.dropna(subset=["clients_target"])
        .drop_duplicates(subset="clients_target")
        .sort_values("clients_target")
    )
    if len(df) < 2:
        raise ValueError("Нужно минимум 2 шага (строки) в df для расчёта рейтинга")

    iterations = df["clients_target"].tolist()
    model = MathModel()

    for name, (weight, bounds) in DHCP_RATING_CRITERIA.items():
        # Нет значения - perfdhcp не получил ответов на шаге (delay "inf"/"n/a"
        # или шаг отсутствует в выводе), поэтому считаем его худшим, а не нулевым
        lower = max(bounds[0], DHCP_RATING_MIN_VALUE)
        values = df[name].fillna(bounds[1]).clip(lower, bounds[1]).tolist()
        model.add_criterion(
            name=name,
            iterations=iterations,
            values=values,
            weight=weight,
            negative=True,
            bounds=bounds,
        )

    total = model.total_rating(power=power).total
    if not math.isfinite(total):
        raise ValueError(f"Рейтинг DHCP не посчитался: total={total}")

    # К рейтингу дописывается цифра единиц исходного total: 561 + 56100302176 -> 5616
    return round(total / DHCP_RATING_SCALE) * 10 + int(total) % 10


def update_dhcp_results_with_rating(results_path: str = DHCP_RESULTS) -> pd.DataFrame:
    df = build_dhcp_dataframe(results_path)
    try:
        rating = get_dhcp_total_rating(df)
    except ValueError as e:
        # Без рейтинга отчёт всё равно публикуется, в json пишется null
        print(f"\n\nОшибка расчёта total_rating DHCP: {e}\n")
        rating = None

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

    total_rating = dhcp_results_dict.get("total_rating")
    if total_rating is None:
        total_rating = "не рассчитан"
    builder.add_heading(text=f"Total rating: {total_rating}", level=2)
    # builder.add_paragraph(str(dhcp_results_dict["total_rating"]))

    builder.add_table(
        {
            "title": "Описание параметров таблицы результатов",
            "headers": ["Параметр", "Описание"],
            "rows": [[header, description] for _, header, description in DHCP_RESULT_COLUMNS],
        }
    )

    result_rows = df[[column for column, _, _ in DHCP_RESULT_COLUMNS]].values.tolist()
    for row in result_rows:
        if not pd.isna(row[0]):
            row[0] = int(row[0])

    builder.add_table(
        {
            "title": "Результаты тестирования по шагам (N клиентов)",
            "headers": [header for _, header, _ in DHCP_RESULT_COLUMNS],
            "rows": result_rows,
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
