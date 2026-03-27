MathModels
=====================

Модуль ``MathModels`` документирует актуальную модель интегрального рейтинга ``MathModel``.

.. note::
   Автор: ``mfilippenko``

------------------------------------------------------------------------------------------------
``MathModel``
------------------------------------------------------------------------------------------------

Класс ``MathModel`` рассчитывает ``total_rating`` по группе критериев.

Основные шаги расчёта для каждого критерия:

* min-max нормализация в рамках ``bounds``
* ориентирование критерия (``negative`` инвертируется)
* полиномиальная аппроксимация (max degree = 10, с понижением при ``RankWarning``)
* интеграл и усреднение по интервалу
* преобразование ``odds``
* степенное преобразование через общий ``power``
* взвешивание и суммирование вкладов

``power`` не хранится внутри модели: его нужно передать в ``total_rating(power=...)``.

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Конструктор
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

``MathModel()`` не принимает обязательных параметров.

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Основные методы
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

* ``add_criterion(name, iterations, values, weight, negative, bounds)``
  Добавляет критерий.

* ``calc_power(sample_count=50, random_seed=42, bounds_margin=0.01, signed_ratio_range=(-4.0, 4.0), ...)``
  Отладочно подбирает коэффициент ``power`` brute-force-поиском.
  Используется для калибровки на базовом наборе.

* ``total_rating(power)``
  Возвращает рейтинг и детализацию по критериям с зафиксированным ``power``.

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Пример: калибровка ``power`` и расчёт рейтинга
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

.. code-block:: python

    from allta import MathModel

    x_values = [100, 200, 300, 400, 500]

    model = MathModel()
    model.add_criterion(
        "la",
        iterations=x_values,
        values=[8.482, 17.97, 31.627, 46.598, 61.064],
        weight=0.33,
        negative=True,
        bounds=(0.0, 700.0),
    )
    model.add_criterion(
        "tps1",
        iterations=x_values,
        values=[11789.016818, 11129.553002, 9485.622431, 8584.144843, 8188.122856],
        weight=0.83,
        negative=False,
        bounds=(0.0, 140000.0),
    )
    model.add_criterion(
        "tps2",
        iterations=x_values,
        values=[11791.092539, 11130.303031, 9486.123688, 8584.491484, 8188.385106],
        weight=0.83,
        negative=False,
        bounds=(0.0, 140000.0),
    )

    debug = model.calc_power(
        sample_count=80,
        random_seed=42,
        signed_ratio_range=(-10.0, 8.0),
        bounds_margin=1e-6,
    )
    fixed_power = debug["power"]

    result = model.total_rating(power=fixed_power)
    print("power:", fixed_power)
    print("total_rating:", result["total_rating"])

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Пример: использование фиксированного ``power``
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

.. code-block:: python

    from allta import MathModel

    x_values = [100, 200, 300, 400, 500]

    model = MathModel()
    model.add_criterion("la", x_values, [5.386, 8.672, 15.003, 18.983, 25.212], 0.33, True, (0.0, 700.0))
    model.add_criterion("tps1", x_values, [18566.441029, 23062.364656, 19995.914244, 21071.687911, 19831.720727], 0.83, False, (0.0, 140000.0))
    model.add_criterion("tps2", x_values, [18568.963702, 23067.077549, 19998.627232, 21074.072323, 19833.653092], 0.83, False, (0.0, 140000.0))

    fixed_power = 0.48
    result = model.total_rating(power=fixed_power)
    print(result["total_rating"])

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Интерактивная документация
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

.. autoclass:: allta.MathModel
   :members:
   :undoc-members:
