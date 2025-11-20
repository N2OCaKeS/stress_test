"""
Инструменты для сборки HTML-страниц отчётов из чистого Python.

Модуль предоставляет класс :class:`PageBuilder`, позволяющий добавлять
заголовки, текстовые блоки, таблицы и галереи без ручного написания
разметки. Это облегчает создание типовых страниц Confluence прямо из
тестов и вспомогательных скриптов.
"""

from collections.abc import Iterable, Mapping, Sequence
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
    .jira-confluence-report .gallery figure {display: flex; flex-direction: column; align-items: center; margin-bottom: 32px;}
    .jira-confluence-report .gallery .gallery-image-title {display: block; font-weight: 600; margin-bottom: 8px; text-align: center;}
    .jira-confluence-report .gallery .gallery-image-caption {display: block; margin-top: 8px; text-align: center; color: #5e6c84; font-style: italic;}
    .jira-confluence-report .gallery.columns-1 {grid-template-columns: 1fr;}
    .jira-confluence-report .gallery.columns-2 {grid-template-columns: repeat(2, 1fr);}
    .jira-confluence-report .gallery.columns-3 {grid-template-columns: repeat(3, 1fr);}
    .jira-confluence-report .report-block {margin: 24px 0;}
    .jira-confluence-report .report-block + .report-block {margin-top: 32px;}
    .jira-confluence-report .report-block-title {display: block; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: #0052CC; margin-bottom: 8px;}
    .jira-confluence-report .chart-title {font-size: 20px; font-weight: 700; margin: 0 auto 12px; color: #172B4D; text-align: center;}
    .jira-confluence-report .attachment-block {border: 1px dashed #dfe1e6; padding: 12px; background: #f7f8fa;}
    .jira-confluence-report .attachment-block .attachment-description {color: #5e6c84; margin-top: 8px;}
    .jira-confluence-report .attachment-image .attachment-image-media {display: block; margin-top: 8px;}
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

        path = self._register_attachment_path(file_path)
        normalized = str(path)
        if not display:
            return self

        filename = path.name or normalized
        title_text = self._coerce_optional_str(title)
        block_title = (
            f"<div class=\"report-block-title\">{escape(title_text)}</div>"
            if title_text
            else ""
        )
        if path.suffix.lower() in self.IMAGE_EXTENSIONS:
            figcaption = (
                f"<figcaption>{escape(str(description))}</figcaption>"
                if description is not None
                else ""
            )
            html = (
                '<figure class="attachment-block attachment-image">'
                f"{block_title}"
                "<div class=\"attachment-image-media\">"
                "<ac:image>"
                f'<ri:attachment ri:filename="{escape(filename)}"/>'
                "</ac:image>"
                "</div>"
                f"{figcaption}"
                "</figure>"
            )
        else:
            description_html = (
                f"<p class=\"attachment-description\">{escape(str(description))}</p>"
                if description is not None
                else ""
            )
            link_text = str(title_text or filename)
            link_html = self._build_attachment_link(filename, link_text)
            html = (
                '<div class="attachment-block attachment-file">'
                f"{block_title}"
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
        """

        normalized_items = self._normalize_gallery_items(images)
        if not normalized_items:
            return self

        columns = max(1, int(columns))
        figure_chunks = []
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
                    f"<{heading_tag} class=\"gallery-image-title\">{escape(title)}</{heading_tag}>"
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
                body_parts.append(attachment_markup)
            else:
                body_parts.append(
                    f'<img src="{escape(src)}" alt="{escape(alt_text)}"/>'
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
                    f"<div class=\"gallery-image-caption\">{caption_block}</div>"
                )

            figure_chunks.append(f"<figure>{''.join(body_parts)}</figure>")

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
                * ``title_level`` — уровень заголовка (``h3`` или ``h4``).
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
        raw_title = chart_spec.get("title")
        title_text = raw_title.strip() if isinstance(raw_title, str) else ""
        title_level = self._normalize_heading_level(
            chart_spec.get("title_level"), default=3, min_level=3, max_level=4
        )
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
        if title_text:
            heading_tag = f"h{title_level}"
            block_parts.append(
                f"<{heading_tag} class=\"chart-title\">{escape(title_text)}</{heading_tag}>"
            )

        macro_parts = ['<ac:structured-macro ac:name="chart">']
        macro_parts.append(
            f'<ac:parameter ac:name="type">{escape(chart_type)}</ac:parameter>'
        )
        if isinstance(raw_title, str):
            macro_parts.append(
                f'<ac:parameter ac:name="title">{escape(raw_title)}</ac:parameter>'
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

        Returns:
            str: Полная HTML-разметка отчёта.
        """

        body = []
        if self.title:
            body.append(f"<h1>{escape(self.title)}</h1><br/>\n")
        body.extend(self._wrap_report_block(chunk) for chunk in self._sections)
        return (
            '<div class="jira-confluence-report">'
            f"<style>{self.DEFAULT_STYLE}</style>\n"
            f"{''.join(body)}"
            "</div>"
        )

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
        block = f'<div class="report-block">{content}</div>'
        return f"{block}<br/>\n"

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

    def _normalize_heading_level(self, level, *, default=3, min_level=1, max_level=6):
        try:
            candidate = int(level)
        except (TypeError, ValueError):
            candidate = default
        return min(max(candidate, min_level), max_level)
