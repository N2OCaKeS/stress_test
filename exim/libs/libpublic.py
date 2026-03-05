import pandas as pd
from typing import Any, Dict, List, Tuple
from allta import PageBuilder, ConfluencePublisher

from exb_conf import MAIL_MAX, MAIL_STEP, REPORT_FILENAME, MAIL_USERS_QTY_MAX, MAIL_USERS_QTY_STEP

def create_table_from_report_file(filename: str = REPORT_FILENAME, type_test: str = "smtp"):
    if type_test == "smtp":
        names = ["email_count", "successfully_sent_emails", "emails_sent_per_second"]
    elif type_test == "imap":
        names = ["user_count", "successful_count", "error_count", "avg_latency", "throughput"]
    df = pd.read_csv(
        filename,
        sep='\\s+',
        header=None,
        names=names
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
    total_rating,
    stand_number,
    lead_time="",
    type_test: str = "smtp",
    test_cycle_version: str | None = None,
):
    preview_path = "report/confluence_report.html"
    reporter = ConfluencePublisher(
        base_url="https://life.astralinux.ru", username=username, token=token
    )
    builder = PageBuilder(title=title)

    if type_test == "smtp":
        description = "Нагрузочное тестирование отправки электронной почты через SMTP. MTA - Exim4.\n"
        labels = [
            {"label": "number of successfully users get emails", "value": "0,2"},
            {"label": "average latency, ms", "value": "0,4"},
            {"label": "throughput, operations/sec", "value": "0,4"},
        ]
        params = [
            {"label": "MAIL_MAX", "value": MAIL_MAX},
            {"label": "MAIL_STEP", "value": MAIL_STEP}
            ]

    elif type_test == "imap":
        description = "Нагрузочное тестирование чтения электронной почты через IMAP. MDA - Dovecot.\n"
        labels = [
            {"label": "number of successfully", "value": "0,2"},
            {"label": "number of emails sent per second", "value": "0,8"},
        ]
        params = [
            {"label": "USER_COUNT", "value": MAIL_USERS_QTY_MAX},
            {"label": "MAIL_STEP", "value": MAIL_USERS_QTY_STEP},
        ]

    header_table = [
        {
            "label": "Ranging",
            "value": {
                "link": "https://life.astralinux.ru/pages/viewpage.action?pageId=150939635",
                "link_text": "Рассчет рейтинга",
                "items": labels,
            },
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
    builder.add_paragraph(text=description)
    builder.add_heading(text=f"Total Rating: {total_rating}", level=2)
    
    table = create_table_from_report_file(type_test=type_test)

    builder.add_table(table_spec=table)

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


