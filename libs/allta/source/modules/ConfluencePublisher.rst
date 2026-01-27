ConfluencePublisher
===================

.. note::
   Автор: команда ``allta``. Класс отвечает за публикацию HTML-страниц и вложений в Confluence, включая построение версионного дерева страниц для stress-отчётов.

Ключевые возможности
--------------------

* Публикация HTML (Confluence Storage format) на страницу Confluence: создание/обновление.
* Прикрепление вложений к странице.
* Добавление меток (labels).
* (Опционально) построение версионного дерева страниц на основе ``test_cycle_version``:

  * ``global``: ``X.Y`` (например, ``1.8``)
  * ``detailed``: ``X.Y.Z`` (например, ``1.8.4`` или ``1.7.5`` для ``1.7.5.UU.2``)
  * ``more``: полная версия как есть (например, ``1.8.4.46`` или ``1.7.5.UU.2``)

* Обход ограничения Confluence на уникальность заголовков в одном space — через **невидимые суффиксы**
  (zero-width символы). Пользователь визуально видит одинаковые названия, но Confluence получает
  уникальные ``title``.

Использование
-------------

Публикация одной страницы (простая)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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
        body="<h1>Demo</h1>",
        parent_id=None,  # публикуем в корень space
        attachments=[Path("report.html")],
        labels=["stress-demo"],
    )

    publisher.attach_files(page_id=page_id, files=["chart.png"])

Публикация с построением версионного дерева
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    publish_result = publisher.publish_results_from_params(
        conf_space="DEVQA",
        conf_parent_page="Системные службы",  # может быть None
        conf_new_page_name="apache-rp_1.8.4.46_smolensk_6.1.158-1-generic_stand12",
        test_cycle_version="1.8.4.46",
        body=builder,
        attachments=[*builder.attachments],
        create_tree=True,
    )

    # publish_result == {"page_id": "...", "release_page_id": "..."}

Поведение ``create_tree=False``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Если ``create_tree=False``, то версионные контейнеры не создаются.
Будет опубликовано только:

* контейнер ``conf_parent_page`` (если задан) — с макросом ``children``
* страница отчёта под ``conf_parent_page`` (если задан), иначе в корень space

Структура создаваемых страниц при ``create_tree=True``
------------------------------------------------------

Пусть:

* ``global`` = ``1.8``
* ``detailed`` = ``1.8.4``
* ``more`` = ``1.8.4.46``
* ``conf_parent_page`` = ``tea`` (или ``None``)

Тогда создаётся:

.. code-block:: text

    STRESS 1.8
      └─ STRESS_REPORT 1.8.4
          ├─ STRESS_REPORT 1.8.4.46
          │   └─ STRESS_REPORT 1.8.4.46 tea
          │       └─ STRESS_REPORT 1.8.4.46 <page>
          └─ STRESS_REPORT 1.8.4 tea
              └─ STRESS_REPORT 1.8.4 <page>

При публикации в ветку ``detailed`` имя страницы автоматически корректируется:

* если ``conf_new_page_name`` содержит ``more``-версию — она заменяется на ``detailed``

Пример:

.. code-block:: text

    apache-rp_1.8.4.46_smolensk_6.1.158-1-generic_stand12
    -> apache-rp_1.8.4_smolensk_6.1.158-1-generic_stand12

Аналогично для вложений:

* если имя файла содержит ``more``-версию — создаётся временная копия с заменой ``more -> detailed``
  и прикрепляется именно она.

Правила обработки ``UU``
------------------------

Для версий вида ``1.7.5.UU.2``:

* ``global`` = ``1.7``
* ``detailed`` = ``1.7.5`` (**первые 3 сегмента**, т.к. ``1.7.UU.*`` не используется)
* ``more`` = ``1.7.5.UU.2``

Невидимые суффиксы и уникальность заголовков
--------------------------------------------

Confluence запрещает одинаковые ``title`` в одном ``space``.
Чтобы можно было иметь страницы с одинаковыми видимыми названиями под разными родителями,
класс добавляет к фактическому заголовку **невидимый суффикс** на основе токена.

Пользователь видит заголовок без изменений, но Confluence различает страницы.

Правило включения имени родителя в токен
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Имя ``conf_parent_page`` добавляется в токен **только** если версия узла — строго ``X.Y.Z``
(три части и все числовые), например ``1.8.4``. Для ``1.8``, ``1.8.4.46`` или ``1.7.5.UU.2`` —
в токен родитель по этому правилу не добавляется.

API
---

------------------------------------------------------------------------------------------------
``__init__(*, base_url: str, username: str, password: str | None = None, token: str | None = None) -> None``
------------------------------------------------------------------------------------------------

Создаёт клиент Confluence. Требует либо пароль, либо токен API.

:Параметры:
  - **base_url** (*str*): URL Confluence (если без схемы, будет добавлено ``https://``).
  - **username** (*str*): имя пользователя.
  - **password** (*str | None*): пароль (если используется).
  - **token** (*str | None*): API token (если используется).

:Исключения:
  - **ValueError**: если не задан ни ``password``, ни ``token``.

------------------------------------------------------------------------------------------------
``publish(*, space: str, title: str, body: str, parent_id: str | None = None, attachments: Sequence[str | Path] | None = None, labels: Sequence[str] | None = None, _effective_title: str | None = None) -> str``
------------------------------------------------------------------------------------------------

Создаёт или обновляет страницу, загружает вложения и метки.

.. important::
   ``title`` — "видимое" имя для логики/логов. Если используется скрытая уникализация,
   внутренне применяется ``_effective_title`` (точный ``title``, который сохранится в Confluence).

:Параметры:
  - **space** (*str*): space key.
  - **title** (*str*): видимый заголовок страницы.
  - **body** (*str*): HTML в формате Confluence Storage.
  - **parent_id** (*str | None*): ID родителя (если ``None`` — создаётся в корне space).
  - **attachments** (*Sequence[str | Path] | None*): файлы для прикрепления.
  - **labels** (*Sequence[str] | None*): метки страницы.
  - **_effective_title** (*str | None*): точный title для Confluence (может содержать невидимые суффиксы).

:Возвращает:
  - *str*: ``page_id``.

------------------------------------------------------------------------------------------------
``attach_files(*, page_id: str, files: Iterable[Path | str]) -> None``
------------------------------------------------------------------------------------------------

Прикрепляет дополнительные файлы к существующей странице.

:Параметры:
  - **page_id** (*str*): ID страницы.
  - **files** (*Iterable[Path | str]*): список/итератор путей к файлам.

------------------------------------------------------------------------------------------------
``publish_results_from_params(*, conf_space: str, conf_parent_page: str | None, conf_new_page_name: str, test_cycle_version: str | None, body: PageBuilder, attachments_dir: Path | str | None = None, attachments: Sequence[str | Path] | None = None, create_tree: bool = True) -> dict[str, str | None]``
------------------------------------------------------------------------------------------------

Публикует результат теста, создавая версионное дерево (если включено) и публикуя:

* основную страницу в ветке ``more`` (полная версия)
* "релизную" копию в ветке ``detailed`` (``X.Y.Z``), если версия распознана

:Параметры:
  - **conf_space** (*str*): space key.
  - **conf_parent_page** (*str | None*): ближайшая родительская страница (может быть ``None``).
  - **conf_new_page_name** (*str*): имя страницы отчёта.
  - **test_cycle_version** (*str | None*): версия тестового цикла (например ``1.8.4.46`` или ``1.7.5.UU.2``).
  - **body** (*PageBuilder*): объект с методом ``render()``.
  - **attachments_dir** (*Path | str | None*): каталог вложений (все файлы будут прикреплены).
  - **attachments** (*Sequence[str | Path] | None*): дополнительные вложения.
  - **create_tree** (*bool*): создавать ли версионное дерево страниц.

:Возвращает:
  - *dict[str, str | None]*:
    - ``page_id``: ID страницы в ветке ``more`` (основная публикация)
    - ``release_page_id``: ID страницы в ветке ``detailed`` или ``None``

:Исключения:
  - **TypeError**: если ``body`` не имеет ``render()``.
  - **ValueError**: если ``conf_new_page_name`` пуст.

------------------------------------------------------------------------------------------------
``_ensure_labels(*, page_id: str, labels: Sequence[str]) -> None``
------------------------------------------------------------------------------------------------

Внутренний метод: настраивает метки страницы.

:Параметры:
  - **page_id** (*str*): ID страницы.
  - **labels** (*Sequence[str]*): список меток.
