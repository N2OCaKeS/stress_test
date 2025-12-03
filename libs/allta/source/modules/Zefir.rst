Zefir
=====

.. note::
   Клиент для Jira/Zefir без привязки к конкретному тесту. Позволяет искать прогоны/кейсы, выставлять статусы и собирать matrix-отчёты.

Использование
-------------

.. code-block:: python

    from allta import ZefirClient, ZefirStatusAPI, ZefirResultTable, UploaderZC

    # Базовый клиент
    client = ZefirClient(
        basic_auth_header="Basic XXX",
        project_id=11200,
        default_user_key="JIRAUSER123",
        default_folder_tree_id=2773,
    )
    runs = client.list_test_runs()  # список прогонов в дереве
    result_id = client.find_test_case_result_id(
        test_cycle_name="1.8.1.o_stand1",
        test_case_name="vpn smoke",
    )
    client.set_test_result(test_result_id=result_id, status="pass")

    # Совместимый слой в стиле старого zefir.py
    status_api = ZefirStatusAPI(
        folder_tree_id=2773,
        basic_auth="Basic XXX",
        test_cycle_name="1.8.1.o_stand1",
        test_case_name="vpn smoke",
        project_id=11200,
        user_key="JIRAUSER123",
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

------------------------------------------------------------------------------------------------
``ZefirClient``
------------------------------------------------------------------------------------------------

Минимальный клиент Jira/Zefir: поиск прогонов и кейсов, обновление статусов, получение matrix-отчётов.

Основные методы:

- ``list_test_runs(folder_tree_id=None, query=None, max_results=400, start_at=0, archived=False)`` — ищет прогоны в дереве или по TQL.
- ``list_test_run_items(test_run_id)`` — элементы прогона с последним результатом.
- ``find_test_case_result_id(test_cycle_name, test_case_name, folder_tree_id=None, query=None)`` — находит id результата по именам.
- ``set_test_result(test_result_id, status, user_key=None, execution_date=None)`` — обновляет статус результата.
- ``fetch_matrix(tql, epic_jql=\"\", jql=\"\", ...)`` — raw matrix-данные.
- ``matrix_to_dataframe(matrix, run_name_parser=None, status_transform=None)`` — преобразование matrix в ``pandas.DataFrame``.

------------------------------------------------------------------------------------------------
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
