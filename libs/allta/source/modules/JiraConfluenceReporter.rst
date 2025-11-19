JiraConfluenceReporter
======================

Модуль ``_jira_confluence_reporter`` содержит высокоуровневые инструменты
для формирования HTML-страниц и публикации их в Confluence без дублирования
кода в тестах.

.. note::
    Автор: ``team13``

------------------------------------------------
``PageBuilder``
------------------------------------------------

Класс отвечает за формирование содержимого типовой страницы отчёта. Всё
управление выполняется из Python-кода без необходимости подключать YAML
или JSON.

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Принимаемые методы:
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

* ``add_heading(text, level=2)`` — добавляет заголовок уровня ``h1-h6``.
* ``add_paragraph(text)`` — формирует текстовый блок с поддержкой переносов строк.
* ``add_unordered_list(items)`` / ``add_ordered_list(items)`` — создаёт
  ненумерованные и нумерованные списки.
* ``add_table(table_spec)`` — строит таблицу из словаря Python
  (заголовки, строки, подписи).
* ``add_gallery(images, columns=2)`` — размещает изображения в виде
  галереи.
* ``add_chart(chart_spec)`` — добавляет макрос Confluence ``chart`` на
  основе переданных табличных данных с поддержкой разных типов (line,
  bar/column, pie и др.) и указанием единиц измерения осей.
* ``add_attachment(file_path, title=None, description=None, display=True)`` —
  регистрирует файл или изображение для загрузки вместе со страницей и
  формирует отдельный блок с ссылкой или встроенной картинкой.  
* ``render_to_file(path)`` — сохраняет готовый HTML в файл для предпросмотра.

Список всех вложений, добавленных через ``add_attachment``, доступен по
  свойству ``builder.attachments``.

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Примеры использования
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

.. code-block:: python

    from _jira_confluence_reporter import PageBuilder

    builder = PageBuilder(title="OpenVPN Stress Demo")
    builder.add_heading("Сводка", level=2)
    builder.add_paragraph("Краткое описание теста")
    builder.add_table({
        "title": "Метрики",
        "headers": ["Метрика", "Значение"],
        "rows": [
            {"Метрика": "Пропускная способность", "Значение": "8.2 Гбит/с"},
            {"Метрика": "Ошибки", "Значение": "0"},
        ],
    })
    builder.add_chart({
        "title": "Throughput per minute",
        "type": "line",
        "x_key": "Минута",
        "series": ["test1", "test2", "test3"],
        "x_label": "Время",
        "x_unit": "мин",
        "y_label": "Пропускная способность",
        "y_unit": "Гбит/с",
        "data": [
            {"Минута": "T+00", "test1": 8.1, "test2": 7.9, "test3": 8.4},
            {"Минута": "T+01", "test1": 8.4, "test2": 8.0, "test3": 8.7},
        ],
    })

    builder.add_chart({
        "title": "Averages per series",
        "type": "column",
        "x_key": "Серия",
        "series": ["Среднее, Гбит/с"],
        "y_label": "Средняя скорость",
        "y_unit": "Гбит/с",
        "data": [
            {"Серия": "test1", "Среднее, Гбит/с": 8.2},
            {"Серия": "test2", "Среднее, Гбит/с": 7.9},
            {"Серия": "test3", "Среднее, Гбит/с": 8.5},
        ],
    })
    builder.render_to_file("report_preview.html")

------------------------------------------------
``ConfluencePublisher``
------------------------------------------------

Класс обеспечивает подключение к Confluence и автоматизирует создание
страниц, загрузку вложений и расстановку меток.

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Основные методы:
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

* ``publish(space, title, body, parent_title=None, attachments=None, labels=None)`` —
  создаёт или обновляет страницу, а также прикрепляет файлы.
* ``attach_files(page_id, files)`` — отдельная функция для загрузки
  вложений.

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Примеры использования
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

.. code-block:: python

    from _jira_confluence_reporter import ConfluencePublisher, PageBuilder

    builder = PageBuilder(title="Demo")
    builder.add_paragraph("Отчёт сформирован автоматически")

    publisher = ConfluencePublisher(
        base_url="https://confluence.example.com",
        username="user",
        token="api-token",
    )

    publisher.publish(
        space="STRESS",
        title="Demo Report",
        parent_title="Релиз 1.0",
        body=builder.render(),
        attachments=["report_preview.html"],
        labels=["stress", "demo"],
    )

