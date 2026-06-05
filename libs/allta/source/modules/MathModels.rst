MathModels
=====================

Модуль ``MathModels`` документирует актуальную модель интегрального рейтинга ``MathModel``.

.. note::
   Автор: ``mfilippenko``

------------------------------------------------------------------------------------------------
``MathModel``
------------------------------------------------------------------------------------------------

Класс ``MathModel`` рассчитывает ``total_rating`` по группе критериев и поддерживает
два режима (параметр ``type`` в конструкторе):

* ``"odds"`` (по умолчанию) — min-max + odds + power;
* ``"ratio"`` — отношение к эталону (как в UnixBench), без границ и power.

Режим ``odds``. Шаги расчёта для каждого критерия:

* min-max нормализация в рамках ``bounds``
* ориентирование критерия (``negative`` инвертируется)
* полиномиальная аппроксимация (max degree = 10, с понижением при ``RankWarning``)
* интеграл и усреднение по интервалу
* преобразование ``odds``
* степенное преобразование через общий ``power``
* взвешивание и суммирование вкладов

``power`` не хранится внутри модели: его нужно передать в ``total_rating(power=...)``.

Режим ``ratio``. Границы и power не нужны — задаётся эталон (референсный прогон):

* для каждого замера ``ratio = value / reference`` (positive) либо
  ``reference / value`` (negative); ``ratio > 1`` лучше эталона, ``< 1`` хуже;
* ``ratio`` зажимается в ``[1/cap, cap]`` (защита от выбросов);
* замеры критерия сводятся геометрическим средним в ``R_i``;
* итог — взвешенное геометрическое среднее ``R_i`` × ``scale``.

На эталоне все ``ratio = 1`` и рейтинг равен ``scale`` (по умолчанию 100). Метрика,
ставшая в N раз лучше, даёт ``ratio = N`` — магнитуда отражается честно, без
перекалибровки границ между прогонами.

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Конструктор
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

``MathModel(type=None)`` — ``None``/``"odds"`` для odds-режима (по умолчанию),
``"ratio"`` для режима отношения к эталону.

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Основные методы
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

* ``add_criterion(name, iterations, values, weight, negative, bounds=None, reference=None)``
  Добавляет критерий. В режиме ``odds`` нужен ``bounds``, в режиме ``ratio`` — ``reference``
  (скаляр или список длины ``values``).

* ``calc_power(sample_count=50, random_seed=42, bounds_margin=0.01, signed_ratio_range=(-4.0, 4.0), ...)``
  Отладочно подбирает коэффициент ``power`` (только для odds-режима).
  Используется для калибровки на базовом наборе.

* ``total_rating(power=None, *, scale=100.0, cap=1000.0)``
  Возвращает ``RatingResult`` — распаковывается как ``total, criteria = total_rating(...)``
  (доступны и атрибуты ``.total`` / ``.criteria``). В режиме ``odds`` требуется ``power``;
  в режиме ``ratio`` ``power`` не нужен, используются ``scale`` и ``cap``. В ratio-режиме
  каждый элемент ``criteria`` содержит ``baseline``, ``result``, ``ratio`` (индекс), ``weight``.

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

    total, criteria = model.total_rating(power=fixed_power)
    print("power:", fixed_power)
    print("total_rating:", total)

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
    total, criteria = model.total_rating(power=fixed_power)
    print(total)

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Пример: режим ``ratio`` (отношение к эталону)
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

.. code-block:: python

    from allta import MathModel

    # Эталон (референсный прогон) фиксируется один раз.
    reference = {
        "latency": [12.0, 14.0, 18.0],            # negative: меньше = лучше
        "throughput": [5000.0, 7300.0, 9300.0],   # positive: больше = лучше
    }

    model = MathModel(type="ratio")
    # Новый прогон: латентность вдвое ниже (лучше), throughput в 1.5 раза выше.
    model.add_criterion(
        "latency",
        iterations=[100, 200, 300],
        values=[6.0, 7.0, 9.0],
        weight=0.4,
        negative=True,
        reference=reference["latency"],
    )
    model.add_criterion(
        "throughput",
        iterations=[100, 200, 300],
        values=[7500.0, 10950.0, 13950.0],
        weight=0.6,
        negative=False,
        reference=reference["throughput"],
    )

    total, criteria = model.total_rating(scale=100.0)   # эталон -> 100, >100 лучше
    print("total_rating:", total)                                 # ~168.3
    print("R latency:   ", criteria["latency"]["ratio"])          # ~2.0
    print("R throughput:", criteria["throughput"]["ratio"])       # ~1.5

    # Таблица BASELINE | RESULT | INDEX (как в UnixBench):
    for name, info in criteria.items():
        print(name, info["baseline"], info["result"], info["ratio"])

``reference`` можно задать и скаляром — тогда он применяется ко всем замерам критерия.

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Интерактивная документация
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

.. autoclass:: allta.MathModel
   :members:
   :undoc-members:
