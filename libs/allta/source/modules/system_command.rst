SystemCommands
=====================

Модуль ``SystemCommands`` содержит функции и классы, отвечающие за выполнение системных команд на локальной машине.



Примеры использования
-------------

.. code-block:: python

    from allta import SystemCommands

    # Пример выполнения команды 'ls -l'
    result = SystemCommand.execute("ls -l")
    print(result)
