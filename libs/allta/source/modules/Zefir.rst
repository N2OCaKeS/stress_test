Zefir
=====

.. note::
   Универсальные обёртки для Jira/Zefir без привязки к конкретному тесту. Позволяют искать прогоны/кейсы, выставлять статусы и собирать matrix-отчёты.

Использование
-------------

.. code-block:: python

    from allta import ZefirStatusAPI, ZefirResultTable, UploaderZC

    # Совместимый слой в стиле example_zephir.py
    status_api = ZefirStatusAPI(
        folder_tree_id=2773,
        basic_auth="Basic XXX",
        test_cycle_name="1.8.1.o_stand1",
        test_case_name="vpn smoke",
    )
    status_api.upload_status("progress")

    # Matrix-отчёт
    table = ZefirResultTable(
        basic_auth="Basic XXX",
        project_id=11200,
        folder_filter="'/stress_test/1.8.1.o/**'",
    )
    matrix_raw = table.fetch()
    df = table.to_dataframe(matrix_raw)

    # Обёртка для обновления статуса
    uploader = UploaderZC(
        folder_tree_id=2773,
        test_cycle_name="1.8.1.o_stand1",
        test_case_name="vpn smoke",
        basic_auth="Basic XXX",
        project_id=11200,
        user_key="JIRAUSER123",
    )
    uploader.upload_test_cycle_status("pass")

``ZefirStatusAPI``
------------------------------------------------------------------------------------------------

Совместимый слой в духе старого ``zefir.py``: ищет кейс по имени и обновляет его статус, не завязан на конкретный тест.

- ``test_case_dates(test_run_id)`` — id/имена/статусы кейсов прогона.
- ``dates_test_cycle()`` — прогоны с вложенными кейсами для указанного дерева.
- ``finder()`` — id результата по имени прогона/кейса.
- ``upload_status(result_status)`` — установка статуса (pass/fail/progress/код).

------------------------------------------------------------------------------------------------
``ZefirResultTable``
------------------------------------------------------------------------------------------------

Универсальный сборщик matrix-отчётов.

- ``fetch()`` — matrix-ответ по TQL фильтру.
- ``to_dataframe(matrix=None)`` — конвертация matrix-ответа (dict или list) в ``pandas.DataFrame``.

------------------------------------------------------------------------------------------------
``UploaderZC``
------------------------------------------------------------------------------------------------

Лёгкая обёртка над ``ZefirStatusAPI`` для совместимости. Имеет метод:

- ``upload_test_cycle_status(zefir_status)`` — выставляет статус результата теста.
