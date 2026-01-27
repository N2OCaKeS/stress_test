from typing import Any, Dict, List, Tuple

from allta import PageBuilder, ConfluencePublisher
from ovpn.result import AnalysisResult
from ovpn.vm_conf import CLIENTS_TOTAL


def _normalize_stats(
    stats: Any,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Any]:

    if isinstance(stats, AnalysisResult):
        base_stats = stats.stats
        stats_table = stats.stats_table
        public_chart_rows = stats.public_chart_rows
        total_rating = stats.total_rating
        return base_stats, stats_table, public_chart_rows, total_rating

    if isinstance(stats, dict):
        base_stats = stats.get("stats", stats)
        stats_table = stats.get("stats_table", [])
        public_chart_rows = stats.get("public_chart_rows", [])
        total_rating = stats.get("total_rating", None)
        return base_stats, stats_table, public_chart_rows, total_rating

    raise TypeError("ovpn_publisher expects AnalysisResult or dict with stats")


def ovpn_publisher(
    username,
    token,
    space,
    parent_title,
    title,
    stats,
    lead_time="",
    test_cycle_version: str | None = None,
):
    preview_path = "./demo_confluence_report.html"
    reporter = ConfluencePublisher(
        base_url="https://life.astralinux.ru", username=username, token=token
    )
    builder = PageBuilder(title=title)

    base_stats, stats_table, public_chart_rows, total_rating = _normalize_stats(stats)
    rating_text = total_rating if total_rating not in (None, "") else "n/a"

    header_table = [
        {
            "label": "Ranging",
            "value": {
                "link": "https://life.astralinux.ru/pages/viewpage.action?pageId=150939635",
                "link_text": "Рассчет рейтинга",
                "items": [
                    {"label": "Успешно подключенные клиенты", "value": "0,4"},
                    {"label": "Не подключенные клиенты", "value": "0,4"},
                    {"label": "Отключившиеся клиенты", "value": "0,2"},
                ],
            },
        },
        {
            "label": "Params",
            "value": {
                "items": [
                    {"label": "сlient_count", "value": CLIENTS_TOTAL},
                    {"label": "client connect per seccond", "value": "120"},
                    {"label": "reconnect", "value": "no"},
                ],
            },
        },
        {
            "label": "ARM",
            "value": {
                "stand_number": "11",
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
        text=f"Нагрузочный тест для отслеживание работоспособности astra-openvpn-server. По сценарию теста к впн серверу подключается {CLIENTS_TOTAL} клиентов со скоростью 120 подкл/мин и на каждом клиенте идет трафик со скоростью 2 мбайт/сек. На графике после набора нагрузки (после 3:40) может быть резкий рост ошибок это связано с особенностью работы нагрузочного скрипта после окончания его работы клиенты могут массово отключаться"
    )
    builder.add_heading(text=f"Total Rating: {rating_text}", level=2)
    table = {
        "title": "Метрики",
        "headers": ["Метрика", "Значение"],
        "rows": stats_table,
    }
    builder.add_table(table_spec=table)

    graphics = [
        {
            "title": "Нагрузка",
            "type": "line",
            "x_key": "T",
            "series": ["Эталон", "Ошибки", "Активные подключения"],
            "series_colors": {
                "throughput": "#0052CC",
                "throughput_p95": "#36B37E",
            },
            "width": 800,
            "height": 360,
            "x_label": "Время",
            "y_label": "Активные подключения",
            "data": public_chart_rows,
            "view_table": False,
        },
    ]
    builder.add_chart(chart_spec=graphics, columns=1)
    builder.render_to_file(path=preview_path)


    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
        create_tree=True,
    )
    return builder, preview_path, publish_result
