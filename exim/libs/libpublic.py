import pandas as pd
from typing import Any, Dict, List, Tuple
from allta import PageBuilder, ConfluencePublisher

from exb_conf import MAIL_MAX, MAIL_STEP, REPORT_FILENAME

def create_table_from_report_file(filename: str = REPORT_FILENAME):
    df = pd.read_csv(
        filename,
        delim_whitespace=True,
        header=None,
        names=["email_count", "successfully_sent_emails", "emails_sent_per_second"]
    )
    
    table = {
        "title": "Метрики",
        "headers": df.columns.tolist(),
        "rows": df.to_dict('records')
    }
    
    return table

def exb_publisher(
    username,
    token,
    space,
    parent_title,
    title,
    stats,
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
            "label": "Ranging",
            "value": {
                "link": "https://life.astralinux.ru/pages/viewpage.action?pageId=150939635",
                "link_text": "Рассчет рейтинга",
                "items": [
                    {"label": "number of successfully sent emails", "value": "0,5"},
                    {"label": "number of emails sent per second", "value": "0,5"},
                ],
            },
        },
        {
            "label": "Params",
            "value": {
                "items": [
                    {"label": "maximum number of emails", "value": MAIL_MAX},
                ],
            },
        },
        {
            "label": "ARM",
            "value": {
                "stand_number": "13",
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
    builder.add_heading(text=f"Total Rating: {rating_text}", level=2)

    table = create_table_from_report_file()

    builder.add_table(table_spec=table)

    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
    )
    return builder, preview_path, publish_result


