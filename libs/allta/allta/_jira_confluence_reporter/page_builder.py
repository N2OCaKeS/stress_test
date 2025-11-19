"""
Инструменты для сборки HTML-страниц отчётов из чистого Python.

Модуль предоставляет класс :class:`PageBuilder`, позволяющий добавлять
заголовки, текстовые блоки, таблицы и галереи без ручного написания
разметки. Это облегчает создание типовых страниц Confluence прямо из
тестов и вспомогательных скриптов.
"""

from collections.abc import Iterable, Mapping, MutableSequence, Sequence
from html import escape
from pathlib import Path


class PageBuilder:
    """
    Конструктор HTML-страниц для публикации в Confluence.

    Класс работает с привычными python-структурами данных и предоставляет
    методы для последовательного добавления контента. Готовый результат
    можно отрендерить в строку или сохранить в файл для предпросмотра.
    """

    DEFAULT_STYLE = """
    .jira-confluence-report {font-family: Arial, sans-serif;}
    .jira-confluence-report h1,
    .jira-confluence-report h2,
    .jira-confluence-report h3,
    .jira-confluence-report h4 {color: #172B4D;}
    .jira-confluence-report table {border-collapse: collapse; width: 100%; margin: 16px 0;}
    .jira-confluence-report table caption.report-block-title {caption-side: top; margin-bottom: 8px;}
    .jira-confluence-report table th,
    .jira-confluence-report table td {border: 1px solid #dfe1e6; padding: 8px; text-align: left;}
    .jira-confluence-report figure {margin: 16px 0;}
    .jira-confluence-report figure img {max-width: 100%; height: auto; border: 1px solid #dfe1e6;}
    .jira-confluence-report figure figcaption {text-align: center; color: #5e6c84; margin-top: 4px;}
    .jira-confluence-report .gallery {display: grid; gap: 16px;}
    .jira-confluence-report .gallery.columns-1 {grid-template-columns: 1fr;}
    .jira-confluence-report .gallery.columns-2 {grid-template-columns: repeat(2, 1fr);}
    .jira-confluence-report .gallery.columns-3 {grid-template-columns: repeat(3, 1fr);}
    .jira-confluence-report .report-block {margin: 24px 0;}
    .jira-confluence-report .report-block + .report-block {margin-top: 32px;}
    .jira-confluence-report .report-block-title {display: block; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: #0052CC; margin-bottom: 8px;}
    .jira-confluence-report .chart-title {font-size: 20px; font-weight: 700; margin-bottom: 12px; color: #172B4D;}
    .jira-confluence-report .attachment-block {border: 1px dashed #dfe1e6; padding: 12px; background: #f7f8fa;}
    .jira-confluence-report .attachment-block .attachment-description {color: #5e6c84; margin-top: 8px;}    
    .jira-confluence-report .attachment-block .attachment-description {color: #5e6c84; margin-top: 8px;}    
    """

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp"}

    SUPPORTED_CHART_TYPES = {
        "line": "line",
        "area": "area",
        "bar": "bar",
        "column": "bar",  # не все инстансы Confluence поддерживают column
        "stackedbar": "stackedbar",
        "stackedcolumn": "stackedbar",
        "stackedarea": "stackedarea",
        "pie": "pie",
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
        self._sections.append(f"<h{level}>{escape(text)}</h{level}>")
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
        self._sections.append(f"<p>{prepared}</p>")
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
                ключи ``title``, ``description``, ``headers`` и ``rows``.

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
        description = (
            table_spec.get("description")
            if isinstance(table_spec.get("description"), str)
            else None
        )
        headers = self._normalize_headers(table_spec.get("headers"))
        rows = self._normalize_rows(table_spec.get("rows"), headers)

        html_parts = ["<table>"]
        if title:
            html_parts.append(
                f"<caption class=\"report-block-title\">{escape(title)}</caption>"
            )
        if headers:
            header_cells = "".join(f"<th>{escape(column)}</th>" for column in headers)
            html_parts.append(f"<thead><tr>{header_cells}</tr></thead>")
        if rows:
            body_rows = []
            for row in rows:
                cells = []
                for column in headers or row.keys():
                    cells.append(f"<td>{escape(str(row.get(column, '')))}</td>")
                body_rows.append(f"<tr>{''.join(cells)}</tr>")
            html_parts.append(f"<tbody>{''.join(body_rows)}</tbody>")
        html_parts.append("</table>")

        if description:
            html_parts.append(f"<p><em>{escape(description)}</em></p>")

        self._sections.append("".join(html_parts))
        return self

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

        path = Path(file_path)
        normalized = str(path)
        if normalized not in self._attachment_index:
            self._attachment_index.add(normalized)
            self._attachments.append(path)
        if not display:
            return self

        filename = path.name or normalized
        block_title = escape(title or filename)
        if path.suffix.lower() in self.IMAGE_EXTENSIONS:
            figcaption = (
                f"<figcaption>{escape(str(description))}</figcaption>"
                if description is not None
                else ""
            )
            html = (
                '<figure class="attachment-block attachment-image">'
                f"<span class=\"report-block-title\">{block_title}</span>"
                "<ac:image>"
                f'<ri:attachment ri:filename="{escape(filename)}"/>'
                "</ac:image>"
                f"{figcaption}"
                "</figure>"
            )
        else:
            description_html = (
                f"<p class=\"attachment-description\">{escape(str(description))}</p>"
                if description is not None
                else ""
            )
            link_text = str(title or filename)
            link_html = self._build_attachment_link(filename, link_text)
            html = (
                '<div class="attachment-block attachment-file">'
                f"<span class=\"report-block-title\">{block_title}</span>"
                f"<p class=\"attachment-link\">{link_html}</p>"
                f"{description_html}"
                "</div>"
            )

        self._sections.append(html)
        return self


    def add_gallery(self, images, columns=2):
        """
        Добавляет сетку изображений (галерею).

        Args:
            images (Sequence[Mapping[str, str]]): Список словарей с ключами
                ``src`` (путь к файлу), ``caption`` и ``description``.
            columns (int): Количество колонок в сетке.

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.
        """

        columns = max(1, int(columns))
        figure_chunks = []
        for item in images:
            if not isinstance(item, Mapping):
                continue
            src = escape(item.get("src", ""))
            if not src:
                continue
            caption = escape(item.get("caption", ""))
            description = escape(item.get("description", ""))
            figure_body = [f'<img src="{src}" alt="{caption}"/>']

            if caption or description:
                caption_text = caption
                if description:
                    caption_text = f"{caption}<br/><span>{description}</span>"
                figure_body.append(f"<figcaption>{caption_text}</figcaption>")
            figure_chunks.append(f"<figure>{''.join(figure_body)}</figure>")
        gallery = (
            f'<div class="gallery columns-{columns}" '
            f'style="grid-template-columns: repeat({columns}, 1fr);">'
            f"{''.join(figure_chunks)}"
            "</div>"
        )
        self._sections.append(gallery)
        return self

    def add_chart(self, chart_spec):
        """
        Встраивает макрос Confluence ``chart`` с табличными данными.

        Args:
            chart_spec (Mapping[str, object]): Параметры графика. Поддерживаемые
                ключи:

                * ``title`` — заголовок графика.
                * ``type`` — тип графика (``line``, ``bar``, ``column``, ``area``,
                  ``pie`` и другие поддерживаемые Confluence варианты).
                * ``x_key`` — имя колонки, используемой на оси X.
                * ``series`` — последовательность колонок для построения серий.
                * ``width``/``height`` — размеры области графика в пикселях.
                * ``params`` — произвольные параметры макроса ``chart``.
                * ``data`` — список словарей с исходными значениями.
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
                * ``colors`` — список цветов (HTML/HEX), назначаемых сериями.

        Returns:
            PageBuilder: Текущий экземпляр для чейнинга вызовов.
        """

        if not isinstance(chart_spec, Mapping):
            raise TypeError("chart_spec must be a mapping")

        data_rows = self._normalize_chart_data(chart_spec.get("data"))
        if not data_rows:
            return self

        x_key = str(chart_spec.get("x_key") or next(iter(data_rows[0].keys())))
        series_keys = self._normalize_headers(chart_spec.get("series"))
        if not series_keys:
            series_keys = [key for key in data_rows[0].keys() if key != x_key]
        if not series_keys:
            return self

        chart_type = self._normalize_chart_type(chart_spec.get("type"))
        title = chart_spec.get("title")
        width = chart_spec.get("width")
        height = chart_spec.get("height")
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

        if "dataOrientation" in extra_params:
            data_orientation = str(extra_params["dataOrientation"])
        else:
            data_orientation = (
                "horizontal" if series_orientation == "rows" else "vertical"
            )

        series_label = chart_spec.get("series_label")
        colors = chart_spec.get("colors")
        color_list = []
        if isinstance(colors, str):
            color_list = [colors]
        elif isinstance(colors, Iterable):
            color_list = [str(color).strip() for color in colors if str(color).strip()]

        table_html = self._build_chart_table(
            x_key=x_key,
            series_keys=series_keys,
            rows=data_rows,
            orientation=series_orientation,
            series_label=series_label,
        )

        block_parts = []
        if isinstance(title, str) and title.strip():
            block_parts.append(
                f"<h3 class=\"chart-title\">{escape(title.strip())}</h3>"
            )      

        macro_parts = ['<ac:structured-macro ac:name="chart">']
        macro_parts.append(
            f'<ac:parameter ac:name="type">{escape(chart_type)}</ac:parameter>'
        )
        if isinstance(title, str):
            macro_parts.append(
                f'<ac:parameter ac:name="title">{escape(title)}</ac:parameter>'
            )
        if width:
            macro_parts.append(
                f'<ac:parameter ac:name="width">{escape(str(width))}</ac:parameter>'
            )
        if height:
            macro_parts.append(
                f'<ac:parameter ac:name="height">{escape(str(height))}</ac:parameter>'
            )
        if "dataDisplay" not in extra_params:
            macro_parts.append(
                '<ac:parameter ac:name="dataDisplay">table</ac:parameter>'
            )
        if "timeSeries" not in extra_params:
            macro_parts.append(
                f'<ac:parameter ac:name="timeSeries">{str(time_series).lower()}</ac:parameter>'
            )
        if x_label:
            macro_parts.append(
                f'<ac:parameter ac:name="xLabel">{escape(x_label)}</ac:parameter>'
            )
        if y_label:
            macro_parts.append(
                f'<ac:parameter ac:name="yLabel">{escape(y_label)}</ac:parameter>'
            )
        if color_list and "colors" not in extra_params:
            macro_parts.append(
                f"<ac:parameter ac:name=\"colors\">{escape(','.join(color_list))}</ac:parameter>"
            )

        if "dataOrientation" not in extra_params:
            macro_parts.append(
                f'<ac:parameter ac:name="dataOrientation">{escape(data_orientation)}</ac:parameter>'
            )

        # остальные параметры из params
        for name, value in extra_params.items():
            macro_parts.append(
                f'<ac:parameter ac:name="{escape(str(name))}">{escape(str(value))}</ac:parameter>'
            )

        macro_parts.append("<ac:rich-text-body>")
        macro_parts.append(table_html)
        macro_parts.append("</ac:rich-text-body>")
        macro_parts.append("</ac:structured-macro>")

        block_parts.append("".join(macro_parts))
        self._sections.append("".join(block_parts))
        return self

    # ----- rendering -------------------------------------------------------
    def render(self):
        """
        Возвращает итоговый HTML-код страницы.
        """

        body = []
        if self.title:
            body.append(f"<h1>{escape(self.title)}</h1>")
        body.extend(f'<div class="report-block">{chunk}</div>' for chunk in self._sections)
        return (
            '<div class="jira-confluence-report">'
            f"<style>{self.DEFAULT_STYLE}</style>"
            f"{''.join(body)}"
            "</div>"
        )
    @property
    def attachments(self):
        """Возвращает список зарегистрированных вложений."""

        return list(self._attachments)    

    def render_to_file(self, path):
        """
        Сохраняет HTML в файл для локального предпросмотра.

        Args:
            path (str | Path): Путь до файла назначения.
        """

        target = Path(path)
        target.write_text(self.render(), encoding="utf-8")
        return target

    # ----- private helpers -------------------------------------------------
    def _render_list(self, items, ordered):
        tag = "ol" if ordered else "ul"
        entries = (f"<li>{escape(str(item))}</li>" for item in items)
        return f"<{tag}>{''.join(entries)}</{tag}>"
    
    def _build_attachment_link(self, filename, link_text):
        safe_filename = escape(filename)
        return (
            "<ac:link>"
            f'<ri:attachment ri:filename="{safe_filename}"/>'
            f"<ac:plain-text-link-body><![CDATA[{link_text}]]></ac:plain-text-link-body>"
            "</ac:link>"
        )    

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
        body_rows = []
        for row in rows:
            cells = [escape(str(row.get(x_key, "")))]
            for series in series_keys:
                cells.append(escape(str(row.get(series, ""))))
            body_rows.append(
                "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
            )
        header_html = "".join(f"<th>{cell}</th>" for cell in header_cells)
        return (
            "<table>"
            + "<thead><tr>"
            + header_html
            + "</tr></thead>"
            + "<tbody>"
            + "".join(body_rows)
            + "</tbody></table>"
        )

    def _build_chart_table_rows(self, x_key, series_keys, rows, series_label):
        label = (
            str(series_label).strip()
            if isinstance(series_label, str) and series_label.strip()
            else "Серия"
        )
        x_values = [escape(str(row.get(x_key, ""))) for row in rows]
        header_cells = [escape(label)] + x_values

        body_rows = []
        for series in series_keys:
            cells = [escape(series)]
            for row in rows:
                cells.append(escape(str(row.get(series, ""))))
            body_rows.append(
                "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
            )

        header_html = "".join(f"<th>{cell}</th>" for cell in header_cells)
        return (
            "<table>"
            + "<thead><tr>"
            + header_html
            + "</tr></thead>"
            + "<tbody>"
            + "".join(body_rows)
            + "</tbody></table>"
        )

    def _normalize_chart_type(self, raw_type):
        if not raw_type:
            return "line"
        normalized_key = str(raw_type).strip().lower().replace("-", "").replace("_", "")
        return self.SUPPORTED_CHART_TYPES.get(normalized_key, "line")

    def _compose_axis_label(self, label, unit):
        label_text = str(label).strip() if isinstance(label, str) else ""
        unit_text = str(unit).strip() if isinstance(unit, str) else ""
        if label_text and unit_text:
            return f"{label_text} ({unit_text})"
        if unit_text and not label_text:
            return unit_text
        return label_text
