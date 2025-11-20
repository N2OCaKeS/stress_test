ConfluencePublisher
=====================

.. note::
   Автор: команда ``allta``. Класс отвечает за публикацию HTML-страниц и вложений в Confluence.

Использование
-------------

.. code-block:: python

    from allta import ConfluencePublisher
    from pathlib import Path

    publisher = ConfluencePublisher(
        base_url="https://wiki.example.org",
        username="bot",
        token="SECRET",
    )
    page_id = publisher.publish(
        space="DEMO",
        title="Stress Report",
        parent_title="Automation",
        body="<h1>Demo</h1>",
        attachments=[Path("report.html")],
        labels=["stress-demo"],
    )
    publisher.attach_files(page_id=page_id, files=["chart.png"])

------------------------------------------------------------------------------------------------
``__init__(*, base_url, username, password=None, token=None)``
------------------------------------------------------------------------------------------------

Создаёт клиент Confluence. Требует либо пароль, либо токен API.

------------------------------------------------------------------------------------------------
``publish(space, title, body, parent_title=None, attachments=None, labels=None)``
------------------------------------------------------------------------------------------------

Создаёт или обновляет страницу, загружает вложения и метки. Возвращает ``page_id``.

------------------------------------------------------------------------------------------------
``attach_files(page_id, files)``
------------------------------------------------------------------------------------------------

Прикрепляет дополнительные файлы к странице.

------------------------------------------------------------------------------------------------
``_ensure_labels(page_id, labels)``
------------------------------------------------------------------------------------------------

Внутренний метод: настраивает метки страницы.
