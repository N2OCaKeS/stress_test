"""
Инструменты для сборки HTML-страниц отчётов из чистого Python.

Модуль предоставляет класс :class:`PageBuilder`, позволяющий добавлять
заголовки, текстовые блоки, таблицы, галереи и графики Table Filter and
Charts (макрос ``table-chart``) без ручного написания разметки. Это
облегчает создание типовых страниц Confluence прямо из тестов и
вспомогательных скриптов.

Полные примеры структур, которые ожидают методы::

    header_rows = [
        {"label": "Params", "value": {"items": ["Users: 3000", "Env: prod"]}},
        {"label": "ARM", "value": {"stand_number": "10"}},
        {"label": "Lead time", "value": "00:12:34"},
        {"label": "Links", "value": {"text": "Build 42", "link": "https://ci/job/42"}},
        {"label": "Notes", "value": ["ok", "no issues"]},
    ]

    table_spec = {
        "title": "Metrics",
        "title_level": 3,
        "description": "Aggr values",
        "headers": ["Metric", "Value", "Unit"],
        "rows": [
            {"Metric": "TPS", "Value": 42, "Unit": "ops/s"},
            {"Metric": "Latency p95", "Value": 120, "Unit": "ms"},
        ],
    }

    chart_spec = {
        "title": "Throughput",
        "type": "line",
        "x_key": "time",
        "series": ["tps", "latency_p95"],
        "x_label": "Time, s",
        "y_label": "Value",
        "colors": ["#0052CC", "#FF5630"],
        "data": [
            {"time": "00:00", "tps": 1000, "latency_p95": 80},
            {"time": "00:30", "tps": 1200, "latency_p95": 90},
        ],
        "view_table": True,
    }

    gallery_items = [
        {"title": "CPU", "src": "cpu.png", "caption": "Host CPU"},
        {"title": "Memory", "src": "mem.png", "description": "Usage over time"},
    ]

    builder = PageBuilder(title="Demo")
    builder.add_header_table(header_rows)
    builder.add_table(table_spec)
    builder.add_chart(chart_spec)
    builder.add_gallery(gallery_items, columns=2)
    builder.add_attachment("/tmp/raw-data.zip", title="Raw data")
    html = builder.render()
"""

import os
import re
import secrets
import time
from collections.abc import Iterable, Mapping, Sequence
from html import escape
from pathlib import Path
import requests


class PageBuilder:
    """
    Конструктор HTML-страниц для публикации в Confluence.

    Класс работает с привычными python-структурами данных и предоставляет
    методы для последовательного добавления контента.

    Пример::

        builder = PageBuilder(title="Demo")
        builder.add_heading("Кратко")
        builder.add_header_table(
            [
                {"label": "Params", "value": {"items": ["foo", "bar"]}},
                {"label": "ARM", "value": {"stand_number": "10"}},
            ]
        )
        builder.add_table({"headers": ["Metric", "Value"], "rows": [["TPS", 42]]})
        html = builder.render()
    """



    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp"}

    SUPPORTED_CHART_TYPES = {
        "line": "Line",
        "area": "Area",
        "bar": "Bar",
        "column": "Column",
        "pie": "Pie",
    }

    _DEFAULT_CHART_COLORS = [
        "#0052CC", "#FF5630", "#36B37E", "#FFAB00", "#6554C0",
        "#FF8B00", "#00B8D9", "#172B4D", "#8777D9", "#FF7452",
        "#2684FF", "#5243AA", "#79F2C0", "#FFC400", "#57D9A3",
        "#FFBDAD", "#EAE6FF", "#ABF5D1", "#C0B6F2", "#FDD0B5",
        "#1F845A", "#BF2600", "#0065FF",
    ]

    _ROOT_STYLE = (
        "font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: #091e42; "
        "text-align: left; margin: 0; width: 100%;"
    )
    _TITLE_STYLE = "color: #172B4D; font-size: 28px; font-weight: 700; margin: 0 0 16px 0;"
    _HEADING_STYLE = "color: #172B4D; margin: 0 0 8px 0; font-weight: 700;"
    _PARAGRAPH_STYLE = "margin: 8px 0; line-height: 1.5; color: #172B4D;"
    _REPORT_BLOCK_STYLE = "margin: 0;"
    _REPORT_BLOCK_TITLE_STYLE = (
        "display: block; font-weight: 700; font-size: 20px; color: #172B4D; margin: 0 0 12px 0;"
    )
    _TABLE_STYLE = "border-collapse: collapse; width: auto; max-width: 100%; margin: 16px 0;"
    _TABLE_CAPTION_STYLE = _REPORT_BLOCK_TITLE_STYLE
    _TABLE_HEADER_CELL_STYLE = (
        "border: 1px solid #dfe1e6; padding: 8px; text-align: left; background: #edf2ff; font-weight: 600;"
    )
    _TABLE_CELL_STYLE = "border: 1px solid #dfe1e6; padding: 8px; text-align: left;"
    _TABLE_CHART_DELIMITER = "‚"
    _TABLE_CHART_VERSION = "3"
    _TABLE_CHART_WORKLOG = "365|5|8|y w d h m|y w d h m"
    _TABLE_CHART_DATE_PATTERN = "d M yy 'г'."
    _CHART_TITLE_STYLE = "font-size: 20px; font-weight: 700; margin: 0 auto 12px; color: #172B4D; text-align: center;"
    _CHART_WRAPPER_STYLE = (
        "display: flex; justify-content: center; align-items: center; width: 100%; max-width: 100%;"
    )
    _CHART_DEFAULT_WIDTH = 760
    _CHART_DEFAULT_HEIGHT = 360
    _GRID_TABLE_STYLE = (
        "display:inline-table; width:max-content; max-width:100%; border-collapse: separate; "
        "border-spacing: 16px 8px; margin: 8px 0; table-layout: auto;"
    )
    _GRID_CELL_STYLE = "vertical-align: top; text-align: center;"
    _GALLERY_FIGURE_STYLE = "display: inline-flex; flex-direction: column; align-items: center; width: 100%;"
    _GALLERY_TITLE_STYLE = "display: block; font-weight: 600; margin-bottom: 8px; text-align: center;"
    _GALLERY_IMAGE_STYLE = "max-width: 100%; height: auto; border: 1px solid #dfe1e6;"
    _GALLERY_CAPTION_STYLE = "display: block; margin-top: 8px; text-align: center; color: #5e6c84; font-style: italic;"
    _ATTACHMENT_BLOCK_STYLE = "border: 1px dashed #dfe1e6; padding: 12px; background: #f7f8fa; margin: 16px 0;"
    _ATTACHMENT_DESCRIPTION_STYLE = "color: #5e6c84; margin-top: 8px;"
    _ATTACHMENT_IMAGE_MEDIA_STYLE = "display: block; margin-top: 8px;"
    _ATTACHMENT_LINK_CONTAINER_STYLE = "margin: 0;"
    _DETAILS_TABLE_STYLE = (
        "width:auto;max-width:100%;border-collapse:collapse;background:#d9e1f2;"
        "font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif;font-size:15px;color:#091e42;margin:16px 0;"
    )
    _DETAILS_KEY_STYLE = (
        "font-weight:700;width:22%;max-width:260px;white-space:nowrap;border:1px solid #b9c6ec;"
        "padding:10px 16px;vertical-align:top;"
    )
    _DETAILS_VALUE_STYLE = "border:1px solid #b9c6ec;padding:10px 16px;vertical-align:top;font-size:15px;"
    _DETAILS_LINK_STYLE = "color:#0052CC;text-decoration:none;"
    _DETAILS_LIST_STYLE = "margin:6px 0 0 18px;padding:0;"
    _ARM_INFO_URL = "https://life.astralinux.ru/pages/viewpage.action?pageId=192234259"
    _ARM_API_URL = "http://allta.devos.astralinux.ru:21501/api/server/v1/arm/"
    _ARM_CATALOG = {
        "1": {
            "grade": "VM Test WorkStation",
            "cpu": "vCPU (16 cores)",
            "ram": "128Gb",
            "storage": "100Gb",
        },
        "2": {
            "grade": "VM Test WorkStation",
            "cpu": "vCPU (16 cores)",
            "ram": "128Gb",
            "storage": "100Gb",
        },
        "3": {
            "grade": "LowServer",
            "cpu": "Intel(R) Xeon(R) Silver 4110 CPU @ 2.10GHz",
            "ram": "128Gb",
            "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
        },
        "4": {
            "grade": "MiddleServer",
            "cpu": "Intel(R) Xeon(R) CPU E5-2697 v3 @ 2.60GHz",
            "ram": "256Gb",
            "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
        },
        "5": {
            "grade": "HighServer",
            "cpu": "Intel(R) Xeon(R) Gold 5320 CPU @ 2.20GHz",
            "ram": "1024Gb",
            "storage": "nvme0n1 3.2Tb",
        },
        "6": {
            "grade": "VM TestStation",
            "cpu": "vCPU (16 cores)",
            "ram": "128Gb",
            "storage": "100Gb",
        },
        "7": {
            "grade": "VM TestStation",
            "cpu": "vCPU (16 cores)",
            "ram": "128Gb",
            "storage": "100Gb",
        },
        "8": {
            "grade": "VM TestStation",
            "cpu": "vCPU (16 cores)",
            "ram": "128Gb",
            "storage": "100Gb",
        },
        "9": {
            "grade": "VM TestStation",
            "cpu": "vCPU (16 cores)",
            "ram": "128Gb",
            "storage": "100Gb",
        },
        "10": {
            "grade": "LowServer",
            "cpu": "Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz",
            "ram": "128Gb",
            "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
        },
        "11": {
            "grade": "LowServer",
            "cpu": "Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz",
            "ram": "128Gb",
            "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
        },
        "12": {
            "grade": "LowServer",
            "cpu": "Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz",
            "ram": "128Gb",
            "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
        },
        "13": {
            "grade": "LowServer",
            "cpu": "Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz",
            "ram": "128Gb",
            "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
        },
    }

    def __init__(self, title=None):
        self.title = title
        self._sections = []
        self._attachments = []
        self._attachment_index = set()

    # ----- generic helpers -------------------------------------------------
    def add_raw_html(self, html):
        """
        Добавляет произвольный HTML-фрагмент без обработки.

        Args:
            html (str): Строка с уже готовой разметкой.

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.
        """

        self._sections.append(html)
        return self

    def add_heading(self, text, level=2):
        """
        Добавляет заголовок указанного уровня.

        Args:
            text (str): Текст заголовка.
            level (int): Уровень заголовка (1–6). Значение автоматически
                ограничивается указанным диапазоном.

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.
        """

        level = min(max(level, 1), 6)
        self._sections.append(f'<h{level} style="{self._HEADING_STYLE}">{escape(text)}</h{level}>')
        return self

    def add_paragraph(self, text):
        """
        Добавляет текстовый блок с поддержкой переводов строк.

        Args:
            text (str): Текст параграфа. Символы переноса строки
                автоматически преобразуются в ``<br/>``.

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.
        """

        prepared = "<br/>".join(escape(chunk) for chunk in text.splitlines())
        self._sections.append(f'<p style="{self._PARAGRAPH_STYLE}">{prepared}</p>')
        return self

    def add_unordered_list(self, items):
        """
        Добавляет ненумерованный список.

        Args:
            items (Iterable[str]): Коллекция строк для отображения.

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.
        """

        self._sections.append(self._render_list(items, ordered=False))
        return self

    def add_ordered_list(self, items):
        """
        Добавляет нумерованный список.

        Args:
            items (Iterable[str]): Коллекция строк для отображения.

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.
        """

        self._sections.append(self._render_list(items, ordered=True))
        return self

    def add_table(self, table_spec):
        """
        Добавляет таблицу, описанную словарём Python.

        Args:
            table_spec (Mapping[str, object]): Описание таблицы. Поддерживаются
                ключи ``title``, ``title_level``, ``description``, ``headers`` и ``rows``.
                ``title_level`` позволяет выбрать ``h3`` или ``h4`` для заголовка.
                Пример структуры::

                    table_spec = {
                        "title": "Metrics",
                        "title_level": 3,
                        "description": "Aggregated values",
                        "headers": ["Metric", "Value", "Unit"],
                        "rows": [
                            {"Metric": "TPS", "Value": 42, "Unit": "ops/s"},
                            {"Metric": "Latency p95", "Value": 120, "Unit": "ms"},
                        ],
                    }

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.
        """

        if not isinstance(table_spec, Mapping):
            raise TypeError("table_spec must be a mapping")

        title = (
            table_spec.get("title")
            if isinstance(table_spec.get("title"), str)
            else None
        )
        title_level = self._normalize_heading_level(
            table_spec.get("title_level"), default=3, min_level=3, max_level=4
        )
        description = (
            table_spec.get("description")
            if isinstance(table_spec.get("description"), str)
            else None
        )
        headers = self._normalize_headers(table_spec.get("headers"))
        rows = self._normalize_rows(table_spec.get("rows"), headers)

        html_parts = []
        if title:
            heading_tag = f"h{title_level}"
            html_parts.append(
                f'<{heading_tag} style="{self._HEADING_STYLE}">{escape(title)}</{heading_tag}>'
            )

        html_parts.append(f'<table style="{self._TABLE_STYLE}">')
        if headers:
            header_cells = "".join(
                f'<th style="{self._TABLE_HEADER_CELL_STYLE}">{escape(column)}</th>'
                for column in headers
            )
            html_parts.append(f"<thead><tr>{header_cells}</tr></thead>")
        if rows:
            body_rows = []
            for row in rows:
                cells = []
                for column in headers or row.keys():
                    cells.append(
                        f'<td style="{self._TABLE_CELL_STYLE}">{escape(str(row.get(column, "")))}</td>'
                    )
                body_rows.append(f"<tr>{''.join(cells)}</tr>")
            html_parts.append(f"<tbody>{''.join(body_rows)}</tbody>")
        html_parts.append("</table>")

        if description:
            html_parts.append(f"<p><em>{escape(description)}</em></p>")

        self._sections.append("".join(html_parts))
        return self

    def add_header_table(self, rows, *, defaults=None):
        """
        Добавляет «шапку» отчёта — таблицу ключ/значение с поддержкой ссылок и списков.

        Таблица автоматически подставляет первые строки ``Astra version`` и ``Kernel``.
        Значение версии берётся из ``/etc/astra_version`` + ``/etc/astra_license`` и
        дополняется режимом в скобках, а версия ядра — из ``uname -r``.

        Строка ``ARM`` заполняется характеристиками железа, если в ``value`` присутствует
        ``stand_number`` (или ``link_text``/``title`` с номером в тексте). Для стенда
        всегда используется ссылка ``https://life.astralinux.ru/pages/viewpage.action?pageId=192234259``.
        Независимо от позиции в списке строк, ``ARM`` будет предпоследней строкой, а
        ``Lead time`` — последней.

        Args:
            rows: Набор строк в виде словарей или кортежей ``(label, value)``.
                Поддерживаемые варианты значений:

                - строка — выводится с экранированием (``\\n`` → ``<br/>``);
                - последовательность — выводится как маркированный список;
                - словарь — можно комбинировать ``text``, ``link``/``href``/``url`` и
                  ``items`` (список для bullet-пунктов).
            defaults: Значения по умолчанию, подставляются, если в строке нет value.

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.

        Пример входных данных::

            rows = [
                {"label": "Params", "value": {"items": ["Users: 3000", "Env: prod"]}},
                {"label": "ARM", "value": {"stand_number": "10"}},
                {"label": "Lead time", "value": "00:12:34"},
                {"label": "Links", "value": {"text": "Build 42", "link": "https://ci/job/42"}},
                {"label": "Notes", "value": ["ok", "no issues"]},
            ]
            builder.add_header_table(rows)
        """

        self._refresh_arm_catalog()

        defaults = defaults or {}
        prepared = self._prepare_header_rows(rows)
        rendered_rows = []

        for entry in prepared:
            if isinstance(entry, Mapping):
                label = entry.get("label") or entry.get("key")
                value = entry.get("value")
            else:
                continue

            if value is None and label in defaults:
                value = defaults[label]
            if label is None:
                continue

            if self._normalize_label(label) == "arm":
                value = self._auto_fill_arm(value)

            cell_html = self._render_details_value(value)
            rendered_rows.append(
                "<tr>"
                f'<td style="{self._DETAILS_KEY_STYLE}">{escape(str(label))}</td>'
                f'<td style="{self._DETAILS_VALUE_STYLE}">{cell_html}</td>'
                "</tr>"
            )

        if not rendered_rows:
            return self

        table_html = (
            f'<table style="{self._DETAILS_TABLE_STYLE}"><tbody>'
            + "".join(rendered_rows)
            + "</tbody></table>"
        )
        self._sections.append(table_html)
        return self

    add_details_table = add_header_table

    def add_attachment(self, file_path, *, title=None, description=None, display=True):
        """
        Регистрирует файл для загрузки вместе со страницей и добавляет блок с ссылкой.

        Args:
            file_path (str | Path): Путь до файла.
            title (str | None): Пользовательский заголовок блока.
            description (str | None): Дополнительное описание.
            display (bool): Если ``False``, файл будет добавлен в список вложений
                без отображения на странице.

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.
        """

        path = self._register_attachment_path(file_path)
        normalized = str(path)
        if not display:
            return self

        filename = path.name or normalized
        title_text = self._coerce_optional_str(title)
        block_title = (
            f'<div style="{self._REPORT_BLOCK_TITLE_STYLE}">{escape(title_text)}</div>'
            if title_text
            else ""
        )
        if path.suffix.lower() in self.IMAGE_EXTENSIONS:
            figcaption = (
                f'<figcaption style="{self._GALLERY_CAPTION_STYLE}">{escape(str(description))}</figcaption>'
                if description is not None
                else ""
            )
            html = (
                f'<figure style="{self._ATTACHMENT_BLOCK_STYLE}">'
                f"{block_title}"
                f'<div style="{self._ATTACHMENT_IMAGE_MEDIA_STYLE}">'
                "<ac:image>"
                f'<ri:attachment ri:filename="{escape(filename)}"/>'
                "</ac:image>"
                "</div>"
                f"{figcaption}"
                "</figure>"
            )
        else:
            description_html = ""
            if description is not None:
                description_html = (
                    f'<p style="{self._ATTACHMENT_DESCRIPTION_STYLE}">{escape(str(description))}</p>'
                )
            link_text = str(title_text or filename)
            link_html = self._build_attachment_link(filename, link_text)
            html = (
                f'<div style="{self._ATTACHMENT_BLOCK_STYLE}">'
                f"{block_title}"
                f'<p style="{self._ATTACHMENT_LINK_CONTAINER_STYLE}">{link_html}</p>'
                f"{description_html}"
                "</div>"
            )

        self._sections.append(html)
        return self


    def add_gallery(self, images, columns=2):
        """
        Добавляет сетку изображений (галерею).

        Args:
            images (Sequence[Mapping[str, object]] | Mapping[str, object]): Описание
                изображений. Можно передать список словарей с ключами ``src``,
                ``title``, ``caption`` и ``description`` либо словарь, в котором
                ключ является заголовком изображения, а значение — путь или
                словарь с дополнительными параметрами. Ключ ``attachment`` можно
                использовать, чтобы явно указать локальный файл-вложение, а
                ``title_level`` позволяет выбрать ``h3`` или ``h4`` для заголовка.
            columns (int): Количество колонок в сетке.

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.

        Пример::

            images = [
                {"title": "CPU", "src": "cpu.png", "caption": "Host CPU"},
                {"title": "Memory", "src": "mem.png", "description": "Usage over time"},
            ]
            builder.add_gallery(images, columns=2)
        """

        normalized_items = self._normalize_gallery_items(images)
        if not normalized_items:
            return self

        columns = max(1, int(columns))
        figures = []
        for item in normalized_items:
            raw_title = item.get("title")
            heading_level = item.get("title_level")
            level = self._normalize_heading_level(
                heading_level, default=3, min_level=3, max_level=4
            )
            heading_tag = f"h{level}"
            title = raw_title
            caption = item.get("caption")
            description = item.get("description")
            src = item["src"]
            attachment_path = item.get("attachment")
            attachment_markup = ""

            body_parts = []
            if title:
                body_parts.append(
                    f'<{heading_tag} style="{self._GALLERY_TITLE_STYLE}">{escape(title)}</{heading_tag}>'
                )

            alt_text = item.get("alt") or caption or title or ""
            if attachment_path is not None:
                stored_path = self._register_attachment_path(attachment_path)
                alt_text = alt_text or stored_path.name
                attachment_markup = (
                    "<ac:image>"
                    f'<ri:attachment ri:filename="{escape(stored_path.name)}"/>'
                    "</ac:image>"
                )
                body_parts.append(
                    f'<div style="text-align:center;width:100%;">{attachment_markup}</div>'
                )
            else:
                body_parts.append(
                    f'<img src="{escape(src)}" alt="{escape(alt_text)}" style="{self._GALLERY_IMAGE_STYLE}"/>'
                )

            if caption or description:
                caption_block = escape(caption) if caption else ""
                if description:
                    description_html = f"<span>{escape(description)}</span>"
                    caption_block = (
                        f"{caption_block}<br/>{description_html}"
                        if caption_block
                        else description_html
                    )
                body_parts.append(
                    f'<div style="{self._GALLERY_CAPTION_STYLE}">{caption_block}</div>'
                )

            figures.append(
                f'<figure style="{self._GALLERY_FIGURE_STYLE}">{"".join(body_parts)}</figure>'
            )

        rows = [
            figures[idx : idx + columns] for idx in range(0, len(figures), columns)
        ]
        cell_style = self._GRID_CELL_STYLE
        table_rows = []
        for row in rows:
            cells = []
            for figure_html in row:
                cells.append(f'<td style="{cell_style}">{figure_html}</td>')
            if len(row) < columns:
                cells.extend(
                    f'<td style="{cell_style}"></td>'
                    for _ in range(columns - len(row))
                )
            table_rows.append("<tr>" + "".join(cells) + "</tr>")

        gallery = (
            f'<table style="{self._GRID_TABLE_STYLE}"><tbody>'
            + "".join(table_rows)
            + "</tbody></table>"
        )
        self._sections.append(gallery)
        return self

    def add_chart(self, chart_spec, *, columns=1):
        """
        Встраивает макрос Table Filter and Charts ``table-chart`` с табличными данными.

        Args:
            chart_spec (Mapping[str, object] | Sequence[Mapping[str, object]]):
                Параметры графика или последовательность параметров. Поддерживаемые
                ключи:

                * ``title`` — заголовок графика.
                * ``title_level`` — уровень заголовка (``h3`` или ``h4``).
                * ``type`` — тип графика (``line``, ``bar``, ``column``, ``area``,
                  ``pie``). Значения автоматически трансформируются под макрос
                  Table Filter and Charts.
                * ``x_key`` — имя колонки, используемой на оси X.
                * ``series`` — последовательность колонок для построения серий.
                * ``width``/``height`` — размеры области графика в пикселях.
                * ``params`` — произвольные параметры макроса ``table-chart``.
                * ``data`` — список словарей с исходными значениями.
                * ``view_table`` — если ``True``, таблица данных отображается под
                  графиком (по умолчанию скрыта).
                * ``x_label``/``y_label`` — подписи осей.
                * ``x_unit``/``y_unit`` — единицы измерения, добавляемые к подписи
                  соответствующей оси.
                * ``time_series`` — режим временных рядов (bool).
                * ``series_orientation`` — ориентация серий (``rows`` или
                  ``columns``). По умолчанию используется ``columns``.
                  Значение влияет и на внутреннюю таблицу, и на параметр
                  Confluence ``dataOrientation`` (rows → horizontal,
                  columns → vertical), если он не переопределён в ``params``.
                * ``series_label`` — заголовок первой колонки при ориентации
                  ``rows`` (по умолчанию ``"Серия"``).
                * ``colors`` — список цветов (HTML/HEX), назначаемых сериями макроса.
                * ``series_colors`` — отображение ``{series_key: color}``.
                * ``per_category_colors`` — отображение ``{x_value: color}`` для
                  подсветки отдельных категорий.

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.

        Пример единственного графика::

            chart_spec = {
                "title": "Throughput",
                "type": "line",
                "x_key": "time",
                "series": ["tps", "latency_p95"],
                "x_label": "Time, s",
                "y_label": "Value",
                "colors": ["#0052CC", "#FF5630"],
                "data": [
                    {"time": "00:00", "tps": 1000, "latency_p95": 80},
                    {"time": "00:30", "tps": 1200, "latency_p95": 90},
                ],
                "view_table": True,
            }
            builder.add_chart(chart_spec)

        Пример нескольких графиков в сетке::

            charts = [
                {"title": "TPS", "type": "line", "x_key": "t", "series": ["tps"], "data": [{"t": 1, "tps": 100}]},
                {"title": "Latency", "type": "column", "x_key": "t", "series": ["p95"], "data": [{"t": 1, "p95": 80}]},
            ]
            builder.add_chart(charts, columns=2)
        """

        if isinstance(chart_spec, Mapping):
            block = self._render_chart_block(chart_spec)
            if block:
                self._sections.append(block)
            return self

        if isinstance(chart_spec, Sequence) and not isinstance(
            chart_spec, (str, bytes, Mapping)
        ):
            rendered = []
            for entry in chart_spec:
                if isinstance(entry, Mapping):
                    block = self._render_chart_block(entry)
                    if block:
                        rendered.append(block)
            if not rendered:
                return self
            columns = max(1, int(columns))
            cell_style = self._GRID_CELL_STYLE
            rows = [
                rendered[idx : idx + columns]
                for idx in range(0, len(rendered), columns)
            ]
            table_rows = []
            for row in rows:
                cells = [
                    f'<td style="{cell_style}">{block}</td>'
                    for block in row
                ]
                if len(row) < columns:
                    cells.extend(
                        f'<td style="{cell_style}"></td>'
                        for _ in range(columns - len(row))
                    )
                table_rows.append("<tr>" + "".join(cells) + "</tr>")
            grid_html = (
                f'<table style="{self._GRID_TABLE_STYLE}"><tbody>'
                + "".join(table_rows)
                + "</tbody></table>"
            )
            self._sections.append(grid_html)
            return self

        raise TypeError(
            "chart_spec must be a mapping or a sequence of mappings"
        )

    # ----- rendering -------------------------------------------------------
    def render(self):
        """
        Возвращает итоговый HTML-код страницы.

        Returns:
            str: Полная HTML-разметка отчёта.
        """

        body = []
        # if self.title:
        #     body.append(f'<h1 style="{self._TITLE_STYLE}">{escape(self.title)}</h1>\n')
        body.extend(self._wrap_report_block(chunk) for chunk in self._sections)
        body.append(self._render_footer_note())
        return f'<div style="{self._ROOT_STYLE}">' + "".join(body) + "</div>"

    @property
    def attachments(self):
        """
        Возвращает список зарегистрированных вложений.

        Returns:
            list[Path]: Копия списка файлов, добавленных через ``add_attachment``.
        """

        return list(self._attachments)

    def render_to_file(self, path):
        """
        Сохраняет HTML в файл для локального предпросмотра.

        Args:
            path (str | Path): Путь до файла назначения.

        Returns:
            Path: Объект ``Path`` с путём до созданного файла.
        """

        target = Path(path)
        target.write_text(self.render(), encoding="utf-8")
        return target

    # ----- private helpers -------------------------------------------------
    def _wrap_report_block(self, content):
        block = f'<div style="{self._REPORT_BLOCK_STYLE}">{content}</div>'
        return f"{block}<br/>\n"

    def _render_footer_note(self):
        note_style = (
            "margin-top:24px;font-size:12px;color:#6b778c;text-align:center;"
            "font-style:italic;"
        )
        note_text = (
            "This page is auto-generated using the allta library | "
            "Данная страница автосгенерированна с помощью библиотеки allta"
        )
        return f'<p style="{note_style}">{note_text}</p>'

    def _prepare_header_rows(self, rows):
        prepared = []
        if rows is None:
            iterable = []
        else:
            iterable = rows
        for entry in iterable:
            if isinstance(entry, Mapping):
                prepared.append(dict(entry))
            elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
                prepared.append({"label": entry[0], "value": entry[1]})
        labels = {
            self._normalize_label(item.get("label") or item.get("key"))
            for item in prepared
            if item.get("label") or item.get("key")
        }
        insert_index = 0
        version = self._detect_astra_version()
        if version and "astra version" not in labels:
            prepared.insert(0, {"label": "Astra version", "value": version})
            labels.add("astra version")
            insert_index = 1
        kernel = self._detect_kernel()
        if kernel and "kernel" not in labels:
            prepared.insert(insert_index, {"label": "Kernel", "value": kernel})
        head_labels = {"astra version", "kernel"}
        head_rows = []
        arm_rows = []
        lead_rows = []
        other_rows = []
        for entry in prepared:
            normalized = self._normalize_label(entry.get("label") or entry.get("key"))
            if normalized in head_labels:
                head_rows.append(entry)
            elif normalized == "arm":
                arm_rows.append(entry)
            elif normalized == "lead time":
                lead_rows.append(entry)
            else:
                other_rows.append(entry)
        return head_rows + other_rows + arm_rows + lead_rows

    def _auto_fill_arm(self, value):
        if isinstance(value, Mapping):
            merged = dict(value)
        else:
            return value
        hint = (
            merged.get("stand_number")
            or merged.get("link_text")
            or merged.get("title")
            or merged.get("text")
            or ""
        )
        match = re.search(r"(\d+)", str(hint))
        if not match:
            return merged
        server = self._ARM_CATALOG.get(match.group(1))
        if not server:
            return merged
        merged.setdefault("stand_number", match.group(1))
        merged.setdefault("link", self._ARM_INFO_URL)
        if server.get("grade"):
            merged["link_text"] = f"{server['grade']} ({match.group(1)})"
        if not merged.get("items") and not merged.get("list"):
            merged["items"] = [
                {"label": "Processor", "value": server["cpu"]},
                {"label": "Memory", "value": server["ram"]},
                {"label": "Storage", "value": server["storage"]},
            ]
        return merged

    def _refresh_arm_catalog(self):
        """
        Пытается подгрузить актуальный каталог ARM из API.
        При недоступности сервера остаётся локальный словарь.
        """
        try:
            response = requests.get(self._ARM_API_URL, timeout=3)
            if response.ok:
                data = response.json()
                if isinstance(data, dict) and data:
                    self._ARM_CATALOG = data
                else:
                    print("ARM API вернуло пустой/некорректный ответ, используем локальные данные")
            else:
                print(f"ARM API недоступно (status {response.status_code}), используем локальные данные")
        except Exception as e:
            print(f"ARM API недоступно ({type(e).__name__}: {e}), используем локальные данные")

    def _detect_astra_version(self):
        version = ""
        mode = ""
        try:
            version = Path("/etc/astra_version").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        try:
            license_text = Path("/etc/astra_license").read_text(encoding="utf-8")
            match = re.search(r"DESCRIPTION=([^\n]+)", license_text)
            if match:
                desc = match.group(1).strip()
                mode_match = re.search(r"\(([^)]+)\)", desc)
                if mode_match:
                    mode = mode_match.group(1).strip()
                elif desc:
                    mode = desc
        except OSError:
            pass
        if version:
            return f"{version}({mode})" if mode else version
        return mode or None

    def _detect_kernel(self):
        try:
            return os.uname().release
        except OSError:
            return None

    def _render_details_value(self, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return "<br/>".join(escape(chunk) for chunk in value.splitlines())
        if isinstance(value, Mapping):
            if "html" in value:
                return str(value["html"])
            parts = []
            text = value.get("text")
            if text:
                parts.append("<br/>".join(escape(chunk) for chunk in str(text).splitlines()))
            link_href = value.get("link") or value.get("href") or value.get("url")
            if link_href:
                link_text = value.get("link_text") or value.get("title") or link_href
                parts.append(
                    f'<a href="{escape(str(link_href))}" style="{self._DETAILS_LINK_STYLE}">{escape(str(link_text))}</a>'
                )
            items = value.get("items") or value.get("list")
            if items:
                parts.append(self._render_bullet_items(items))
            return "".join(parts)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)):
            return self._render_bullet_items(value)
        return escape(str(value))

    def _render_bullet_items(self, items):
        bullets = []
        for item in items:
            if isinstance(item, Mapping):
                label = item.get("label") or item.get("key") or item.get("title")
                val = item.get("value")
                if label is not None and val is not None:
                    content = f"<b>{escape(str(label))}:</b> {escape(str(val))}"
                else:
                    content = escape(str(item.get("text") or item))
            else:
                content = escape(str(item))
            bullets.append(f"<li>{content}</li>")
        return f'<ul style="{self._DETAILS_LIST_STYLE}">' + "".join(bullets) + "</ul>"

    def _render_list(self, items, ordered):
        tag = "ol" if ordered else "ul"
        entries = (f"<li>{escape(str(item))}</li>" for item in items)
        return f"<{tag}>{''.join(entries)}</{tag}>"

    def _normalize_label(self, label):
        if not isinstance(label, str):
            return ""
        return label.strip().lower()

    def _render_chart_block(self, chart_spec):
        data_rows = self._normalize_chart_data(chart_spec.get("data"))
        if not data_rows:
            return ""

        x_key = str(chart_spec.get("x_key") or next(iter(data_rows[0].keys())))
        series_keys = self._normalize_headers(chart_spec.get("series"))
        if not series_keys:
            series_keys = [key for key in data_rows[0].keys() if key != x_key]
        if not series_keys:
            return ""

        chart_type = self._normalize_chart_type(chart_spec.get("type"))
        raw_title = chart_spec.get("title")
        title_text = raw_title.strip() if isinstance(raw_title, str) else ""
        title_level = self._normalize_heading_level(
            chart_spec.get("title_level"), default=3, min_level=3, max_level=4
        )
        requested_width = chart_spec.get("width")
        width_value = self._coerce_positive_int(requested_width)
        if width_value is None:
            width_value = self._CHART_DEFAULT_WIDTH
        requested_height = chart_spec.get("height")
        height_value = self._coerce_positive_int(requested_height)
        if height_value is None:
            height_value = self._CHART_DEFAULT_HEIGHT
        raw_params = chart_spec.get("params")
        extra_params = dict(raw_params) if isinstance(raw_params, Mapping) else {}
        x_label = self._compose_axis_label(
            chart_spec.get("x_label"), chart_spec.get("x_unit")
        )
        y_label = self._compose_axis_label(
            chart_spec.get("y_label"), chart_spec.get("y_unit")
        )
        time_series = bool(chart_spec.get("time_series"))

        series_orientation = str(
            chart_spec.get("series_orientation") or "columns"
        ).lower()
        if series_orientation not in {"rows", "columns"}:
            series_orientation = "columns"

        series_label = chart_spec.get("series_label")
        colors = chart_spec.get("colors")
        color_list = []
        if isinstance(colors, str):
            color_list = [colors]
        elif isinstance(colors, Iterable):
            color_list = [str(color).strip() for color in colors if str(color).strip()]
        series_colors = chart_spec.get("series_colors")
        if not color_list and isinstance(series_colors, Mapping):
            mapped = [
                str(series_colors.get(series)).strip()
                for series in series_keys
                if str(series_colors.get(series) or "").strip()
            ]
            color_list = mapped
        per_category_colors = chart_spec.get("per_category_colors")

        data_orientation = (
            str(extra_params["dataOrientation"])
            if "dataOrientation" in extra_params
            else ("horizontal" if series_orientation == "rows" else "vertical")
        )
        if x_label and "xLabel" not in extra_params:
            extra_params["xLabel"] = x_label
        if y_label and "yLabel" not in extra_params:
            extra_params["yLabel"] = y_label
        if "timeSeries" not in extra_params:
            extra_params["timeSeries"] = str(time_series).lower()
        if "dataOrientation" not in extra_params:
            extra_params["dataOrientation"] = data_orientation
        if "width" not in extra_params:
            extra_params["width"] = width_value
        if "height" not in extra_params:
            extra_params["height"] = height_value

        table_html, x_values, first_column_label = self._build_chart_table(
            x_key=x_key,
            series_keys=series_keys,
            rows=data_rows,
            orientation=series_orientation,
            series_label=series_label,
        )

        view_table = bool(chart_spec.get("view_table"))
        aggregation_items = x_values if series_orientation == "rows" else series_keys
        pie_key_items = series_keys if series_orientation == "rows" else x_values
        aggregation_param = self._join_table_chart_values(aggregation_items)
        pie_keys_param = self._join_table_chart_values(pie_key_items)
        color_columns_param = self._serialize_category_colors(
            per_category_colors, x_key, data_rows, delimiter=self._TABLE_CHART_DELIMITER
        )
        chart_id = extra_params.get("id")
        if chart_id is None:
            chart_id = self._generate_table_chart_id()

        block_parts = []
        if title_text:
            heading_tag = f"h{title_level}"
            block_parts.append(
                f'<{heading_tag} style="{self._CHART_TITLE_STYLE}">{escape(title_text)}</{heading_tag}>'
            )

        macro_parts = ['<ac:structured-macro ac:name="table-chart">']

        def append_param(name, value):
            if value is None:
                return
            text = str(value)
            if text == "":
                return
            macro_parts.append(
                f'<ac:parameter ac:name="{escape(str(name))}">{escape(text)}</ac:parameter>'
            )

        bar_coloring_type = "custom" if color_columns_param else "mono"
        default_params = [
            ("barColoringType", bar_coloring_type),
            ("colorColumns", color_columns_param),
            ("hidecontrols", "true"),
            ("showtableinline", "true" if view_table else "false"),
            ("column", first_column_label),
            ("aggregation", aggregation_param),
            ("type", chart_type),
            ("version", self._TABLE_CHART_VERSION),
            ("colors", ",".join(color_list) if color_list else None),
            ("isFirstTimeEnter", "true"),
            ("datepattern", self._TABLE_CHART_DATE_PATTERN),
            ("pieKeys", pie_keys_param),
            ("id", chart_id),
            ("worklog", self._TABLE_CHART_WORKLOG),
            ("formatVersion", self._TABLE_CHART_VERSION),
        ]
        for name, value in default_params:
            if name in extra_params:
                continue
            append_param(name, value)

        for name, value in extra_params.items():
            append_param(name, value)

        macro_parts.append("<ac:rich-text-body>")
        table_container_style = "" if view_table else ' style="display:none;"'
        macro_parts.append(
            f'<div{table_container_style} data-pb-chart-table="1">{table_html}</div>'
        )
        macro_parts.append("</ac:rich-text-body>")
        macro_parts.append("</ac:structured-macro>")

        macro_html = "".join(macro_parts)
        wrapper_style = self._compose_chart_wrapper_style(width_value, height_value)
        block_parts.append(f'<div style="{wrapper_style}">{macro_html}</div>')
        return "".join(block_parts)
    def _build_attachment_link(self, filename, link_text):
        safe_filename = escape(filename)
        return (
            "<ac:link>"
            f'<ri:attachment ri:filename="{safe_filename}"/>'
            f"<ac:plain-text-link-body><![CDATA[{link_text}]]></ac:plain-text-link-body>"
            "</ac:link>"
        )

    def _register_attachment_path(self, file_path):
        path = Path(file_path)
        normalized = str(path)
        if normalized not in self._attachment_index:
            self._attachment_index.add(normalized)
            self._attachments.append(path)
        return path

    def _normalize_gallery_items(self, images):
        normalized = []
        if isinstance(images, Mapping):
            iterator = images.items()
        elif isinstance(images, Iterable) and not isinstance(images, (str, bytes)):
            iterator = ((None, entry) for entry in images)
        else:
            return normalized

        for default_title, entry in iterator:
            parsed = self._parse_gallery_entry(entry, default_title)
            if parsed:
                normalized.append(parsed)
        return normalized

    def _parse_gallery_entry(self, entry, default_title):
        attachment_path = None
        if isinstance(entry, Mapping):
            raw_src = entry.get("src") or entry.get("path") or entry.get("file")
            attachment_candidate = entry.get("attachment")
            title = self._coerce_optional_str(entry.get("title"))
            caption = self._coerce_optional_str(entry.get("caption"))
            description = self._coerce_optional_str(entry.get("description"))
            alt = self._coerce_optional_str(entry.get("alt"))
            title_level = entry.get("title_level")
        elif isinstance(entry, (str, Path)):
            raw_src = entry
            attachment_candidate = None
            title = None
            caption = None
            description = None
            alt = None
            title_level = None
        else:
            return None

        attachment_path = self._coerce_existing_path(attachment_candidate)
        if attachment_path is None:
            attachment_path = self._coerce_existing_path(raw_src)

        src = self._coerce_optional_str(raw_src)
        if attachment_path is not None:
            src = attachment_path.name
        if not src:
            return None
        default_title_text = self._coerce_optional_str(default_title)
        inferred_alt = alt or caption or title or default_title_text
        return {
            "src": src,
            "title": title or default_title_text,
            "caption": caption,
            "description": description,
            "alt": inferred_alt,
            "attachment": attachment_path,
            "title_level": title_level,
        }

    def _coerce_optional_str(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
        else:
            text = str(value).strip()
        return text or None

    def _coerce_existing_path(self, value):
        if value is None:
            return None
        candidate = Path(value)
        if candidate.exists():
            return candidate
        return None

    def _normalize_headers(self, headers):
        if headers is None:
            return []
        if isinstance(headers, str):
            return [headers]
        return [str(header) for header in headers]

    def _normalize_rows(self, rows, headers):
        normalized = []
        if isinstance(rows, (str, bytes)):
            return normalized
        if not isinstance(rows, Iterable):
            return normalized
        for row in rows:  # type: ignore[assignment]
            if isinstance(row, Mapping):
                normalized.append({str(key): str(value) for key, value in row.items()})
            elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
                normalized.append(
                    {
                        str(headers[idx]): str(value)
                        for idx, value in enumerate(row)
                        if idx < len(headers)
                    }
                )
        return normalized

    def _normalize_chart_data(self, rows):
        normalized = []
        if isinstance(rows, (str, bytes)):
            return normalized
        if not isinstance(rows, Iterable):
            return normalized
        for row in rows:
            if isinstance(row, Mapping):
                normalized.append(dict(row))
        return normalized

    def _build_chart_table(self, x_key, series_keys, rows, orientation, series_label):
        if orientation == "rows":
            return self._build_chart_table_rows(x_key, series_keys, rows, series_label)

        header_cells = [escape(x_key)] + [escape(series) for series in series_keys]
        x_values = [str(row.get(x_key, "")) for row in rows]
        body_rows = []
        for row in rows:
            cells = [escape(str(row.get(x_key, "")))]
            for series in series_keys:
                cells.append(escape(str(row.get(series, ""))))
            body_rows.append(
                "<tr>"
                + "".join(
                    f'<td style="{self._TABLE_CELL_STYLE}">{cell}</td>' for cell in cells
                )
                + "</tr>"
            )
        header_html = "".join(
            f'<th style="{self._TABLE_HEADER_CELL_STYLE}">{cell}</th>'
            for cell in header_cells
        )
        table_html = (
            f'<table style="{self._TABLE_STYLE}">'
            + "<thead><tr>"
            + header_html
            + "</tr></thead>"
            + "<tbody>"
            + "".join(body_rows)
            + "</tbody></table>"
        )
        return table_html, x_values, x_key

    def _build_chart_table_rows(self, x_key, series_keys, rows, series_label):
        label = (
            str(series_label).strip()
            if isinstance(series_label, str) and series_label.strip()
            else "Серия"
        )
        raw_x_values = [str(row.get(x_key, "")) for row in rows]
        header_cells = [escape(label)] + [escape(value) for value in raw_x_values]

        body_rows = []
        for series in series_keys:
            cells = [escape(series)]
            for row in rows:
                cells.append(escape(str(row.get(series, ""))))
            body_rows.append(
                "<tr>"
                + "".join(
                    f'<td style="{self._TABLE_CELL_STYLE}">{cell}</td>' for cell in cells
                )
                + "</tr>"
            )

        header_html = "".join(
            f'<th style="{self._TABLE_HEADER_CELL_STYLE}">{cell}</th>'
            for cell in header_cells
        )
        table_html = (
            f'<table style="{self._TABLE_STYLE}">'
            + "<thead><tr>"
            + header_html
            + "</tr></thead>"
            + "<tbody>"
            + "".join(body_rows)
            + "</tbody></table>"
        )
        return table_html, raw_x_values, label

    def _compose_chart_wrapper_style(self, width, height):
        style_parts = [self._CHART_WRAPPER_STYLE.rstrip()]
        width_value = self._coerce_positive_int(width)
        height_value = self._coerce_positive_int(height)
        if width_value:
            style_parts.append(f"min-width:{width_value}px;")
            style_parts.append(f"max-width:{width_value}px;")
            style_parts.append("margin:0 auto;")
        if height_value:
            style_parts.append(f"min-height:{height_value}px;")
            style_parts.append("padding-bottom:8px;")
        return " ".join(part for part in style_parts if part)

    def _join_table_chart_values(self, values):
        if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
            return ""
        tokens = []
        for value in values:
            text = str(value).strip()
            if text:
                tokens.append(text)
        return self._TABLE_CHART_DELIMITER.join(tokens)

    def _generate_table_chart_id(self):
        timestamp = int(time.time() * 1000)
        random_part = secrets.randbelow(10**10)
        return f"{timestamp}_{random_part:010d}"

    def _coerce_positive_int(self, value):
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return None
        return candidate if candidate > 0 else None

    def _normalize_chart_type(self, raw_type):
        if not raw_type:
            return "Line"
        normalized_key = str(raw_type).strip().lower().replace("-", "").replace("_", "")
        return self.SUPPORTED_CHART_TYPES.get(normalized_key, "Line")

    def _serialize_category_colors(self, colors_mapping, x_key, rows, *, delimiter=";"):
        if not isinstance(colors_mapping, Mapping):
            return ""
        pairs = []
        for row in rows:
            category = row.get(x_key)
            if category is None:
                continue
            color = colors_mapping.get(category)
            if not color:
                continue
            color_text = str(color).strip()
            if not color_text:
                continue
            pairs.append(f"{category}:{color_text}")
        return delimiter.join(pairs)

    def _compose_axis_label(self, label, unit):
        label_text = str(label).strip() if isinstance(label, str) else ""
        unit_text = str(unit).strip() if isinstance(unit, str) else ""
        if label_text and unit_text:
            return f"{label_text} ({unit_text})"
        if unit_text and not label_text:
            return unit_text
        return label_text

    def _normalize_heading_level(self, level, *, default=3, min_level=1, max_level=6):
        try:
            candidate = int(level)
        except (TypeError, ValueError):
            candidate = default
        return min(max(candidate, min_level), max_level)
