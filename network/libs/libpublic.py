# from allta import PageBuilder, ConfluencePublisher Uncomment to work

import pandas as pd
from typing import Any, Dict, List, Tuple
from allta import PageBuilder, ConfluencePublisher

from net_conf import REPORT_FILENAME

def exb_publisher(
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

    # TODO
    params = [
        {"label": "...", "value": ...},
        {"label": "...", "value": ...},
    ]

    header_table = [
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

    # TODO
    init_on_free_on = 0
    init_on_free_off = 0
    diff = 0

    builder.add_table({
        "title": "Результаты тестирования",
        "headers": ["Метрика", "Значение"],
        "rows": [["init_on_free=on MBytes/sec", init_on_free_on], ["init_on_free=off MBytes/sec", init_on_free_off],["Diff %", diff]],
    })

    builder.add_attachment(file_path=REPORT_FILENAME, description="Report file")

    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
    )
    return builder, preview_path, publish_result

