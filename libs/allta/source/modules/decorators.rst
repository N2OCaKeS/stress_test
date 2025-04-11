Decorators
=================

Модуль ``Decorators`` предоставляет декораторы для обработки ошибок и логирования. Это облегчает оборачивание функций в блоки try/except и помогает вести журнал выполнения функций.


Примеры использования
-------------

.. code-block:: python

    from allta import BaseDecorators

    @BaseDecorators.trycorator
    def divide(a, b):
        return a / b

    result = divide(10, 2)
    print(result)
