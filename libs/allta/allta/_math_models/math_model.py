from __future__ import annotations

import binascii
import math
import warnings
from collections.abc import Sequence
from typing import Any, NamedTuple

import numpy as np
from numpy.exceptions import RankWarning


class RatingResult(NamedTuple):
    """Результат ``total_rating``: распаковывается как ``total, criteria = ...``.

    Также доступны атрибуты ``.total`` и ``.criteria``.
    ``criteria`` — словарь по критериям; в ratio-режиме каждый элемент содержит
    ``baseline``, ``result``, ``ratio`` (индекс) и ``weight``.
    """

    total: float
    criteria: dict[str, Any]


Number = float | int
POWER_SAFE_LEVEL_MIN = 0.12
POWER_SAFE_LEVEL_MAX = 0.88
RANDOM_BOUNDS_POWER_MIN = 0.001
RANDOM_BOUNDS_POWER_MAX = 2.0
RANDOM_BOUNDS_POWER_STEPS = 2000
DEFAULT_POWER_RELATIVE_FACTORS: tuple[float, ...] = (
    0.43,
    0.50,
    0.57,
    0.64,
    0.72,
    0.79,
    0.86,
    0.92,
    0.97,
    0.99,
    1.03,
    1.07,
    1.12,
    1.18,
    1.27,
    1.39,
    1.56,
    1.77,
    2.33,
    8.00,
)
DEFAULT_POWER_CENTER_LEVELS: tuple[float, ...] = (
    0.22,
    0.25,
    0.28,
    0.31,
    0.34,
    0.38,
    0.42,
    0.46,
    0.54,
    0.58,
    0.62,
    0.66,
    0.69,
    0.72,
    0.75,
    0.78,
    0.81,
    0.84,
    0.87,
    0.90,
)
DEFAULT_ALT_CALT_FACTORS: tuple[float, ...] = (
    0.43,
    0.50,
    0.57,
    0.64,
    0.72,
    0.79,
    0.86,
    0.92,
    0.97,
    0.99,
    1.03,
    1.07,
    1.12,
    1.18,
    1.27,
    1.39,
    1.56,
    1.77,
    2.01,
    2.33,
)
# Факторы для печати таблицы test_power().
# 1.0 должен быть в центре списка (одинаковое количество значений слева и справа),
# чтобы base-строка была по центру таблицы.
TEST_POWER_TABLE_FACTORS: tuple[float, ...] = (
    0.10,
    0.15,
    0.20,
    0.25,
    0.38,
    0.43,
    0.50,
    0.57,
    0.64,
    0.72,
    1.00,
    1.12,
    1.25,
    1.30,
    1.50,
    1.80,
    2.00,
    2.12,
    2.37,
    2.63,
    3.00,
    3.11,
    3.37,
    3.74,
    4.00,
    6.00,
    8.00,
)


class MathModel:
    """
    Класс MathModel предоставляет интерфейс для расчёта итогового рейтинга по группе критериев
    с использованием min-max нормализации, инверсии negative-критериев, полиномиальной
    аппроксимации, интеграла и степенного преобразования.

    Основные функции:
    - Добавление критериев по оси итераций.
    - Отладочный подбор общей степени степенного преобразования для всей группы критериев.
    - Расчёт итогового рейтинга по всем ранее добавленным критериям.

    Внутри модели используется следующая схема преобразования:
    - ``z = (x - L) / (U - L)``
    - ``u = z`` для positive и ``u = 1 - z`` для negative
    - ``A = ∫ f(x) dx``
    - ``a = A / interval``
    - ``a_clamped = clip(a, epsilon, 1 - epsilon)``
    - ``odds = a_clamped / (1 - a_clamped)``
    - ``A_transformed = odds^power * interval``
    - ``contribution = weight * A_transformed``

    Коэффициент ``power`` подбирается отдельно через ``calc_power(...)`` на synthetic-наборах,
    построенных из ``bounds`` и типа критерия, а затем явно передаётся в
    ``total_rating(power)`` для получения стабильных результатов между запусками теста.

    Поддерживается два режима (параметр ``type`` в конструкторе):

    - ``"odds"`` (по умолчанию) — min-max + odds + power, описанная выше схема.
    - ``"ratio"`` — отношение к эталону (как в UnixBench): без границ и power. Для каждого
      замера ``ratio = value/reference`` (positive) или ``reference/value`` (negative);
      итог — взвешенное геом-среднее ``ratio`` × ``scale``. На эталоне рейтинг = ``scale``.

    Примеры::

        # ODDS (как раньше): нужны bounds и power
        m = MathModel()                       # или MathModel(type="odds")
        m.add_criterion("syscall", iterations=[4, 8], values=[680000, 690000],
                        weight=0.11, negative=False, bounds=(0, 7_000_000))
        total, criteria = m.total_rating(power=0.998)

        # RATIO: нужен reference (эталонный прогон), без bounds и power
        m = MathModel(type="ratio")
        m.add_criterion("syscall", iterations=[4, 8], values=[6_800_000, 6_900_000],
                        weight=0.11, negative=False, reference=[680000, 690000])
        m.add_criterion("latency", iterations=[1, 2, 3], values=[0.6, 0.6, 0.6],
                        weight=0.06, negative=True, reference=[0.3, 0.3, 0.3])
        total, criteria = m.total_rating(scale=100.0)   # эталон -> 100, >100 лучше
        total                                  # итоговый индекс
        criteria["syscall"]["ratio"]           # индекс критерия (здесь ~10.0)
        criteria["syscall"]["baseline"]        # эталон, criteria[...]["result"] — факт
    """

    def __init__(self, type: str | None = None) -> None:
        """
        Создаёт пустую математическую модель.

        Args:
            type: режим расчёта.
                ``None`` / ``"odds"`` — min-max + odds + power (по умолчанию, как раньше):
                    ``add_criterion`` принимает ``bounds``, ``total_rating(power)`` нужен power.
                ``"ratio"`` — отношение к эталону: ``add_criterion`` принимает ``reference``,
                    ``total_rating()`` считает взвешенное геом-среднее отношений (без power).

        Внутренние параметры модели фиксированы:
        - максимальная степень полинома: 10
        - инверсия negative-критериев: включена
        - epsilon для clipping: 1e-6
        """
        if type not in (None, "odds", "ratio"):
            raise ValueError(f"type должен быть None, 'odds' или 'ratio'; получено {type!r}")
        self._mode = "ratio" if type == "ratio" else "odds"
        self._max_degree = 10
        self._invert_negative = True
        self._epsilon = 1e-6
        self._criteria: dict[str, dict[str, Any]] = {}

    def add_criterion(
        self,
        name: str,
        iterations: Sequence[str | Number],
        values: Sequence[Number],
        weight: Number,
        negative: bool,
        bounds: tuple[Number, Number] | None = None,
        reference: Number | Sequence[Number] | None = None,
    ) -> "MathModel":
        """
        Добавляет критерий в модель.

        Поддерживаются два режима нормализации значения:
        - ``bounds`` — min-max + odds-модель (как раньше): нужен для ``total_rating``.
        - ``reference`` — отношение к эталону: для каждого замера считается
          ``ratio = value / reference`` (positive) или ``reference / value`` (negative),
          ``ratio > 1`` лучше эталона, ``< 1`` хуже. Магнитуда честная (×100 -> ratio 100),
          границы и power не нужны. Используется методом ``ratio_index``.

        Можно передать оба: ``bounds`` для odds-расчёта и ``reference`` для ratio-расчёта.

        Args:
            name (str): Название критерия.
            iterations (Sequence[str | Number]): Ось итераций для критерия.
            values (Sequence[Number]): Значения критерия на каждой итерации.
            weight (Number): Вес критерия в итоговом рейтинге.
            negative (bool): Признак negative-критерия. ``True`` если меньше = лучше.
            bounds (tuple[Number, Number] | None): Границы ``(L, U)`` для odds-режима.
            reference (Number | Sequence[Number] | None): Эталон(ы) для ratio-режима.

        Returns:
            MathModel: Текущий экземпляр модели для chaining-вызовов.
        """
        if not name:
            raise ValueError("name не должен быть пустым")
        if len(iterations) != len(values):
            raise ValueError("iterations и values должны быть одинаковой длины")
        if len(iterations) < 2:
            raise ValueError("Для критерия требуется минимум 2 точки")
        if self._mode == "ratio" and reference is None:
            raise ValueError(f"{name}: в режиме type='ratio' нужен reference")
        if self._mode == "odds" and bounds is None:
            raise ValueError(f"{name}: в режиме type='odds' нужен bounds")

        x_arr = np.array([float(x) for x in iterations], dtype=float)
        y_arr = np.array([float(v) for v in values], dtype=float)

        if float(weight) < 0.0:
            raise ValueError(f"{name}: weight должен быть >= 0")
        if np.any(np.diff(x_arr) <= 0.0):
            raise ValueError(f"{name}: iterations должны быть строго возрастающими")

        entry: dict[str, Any] = {
            "name": str(name),
            "iterations": x_arr,
            "values": y_arr,
            "weight": float(weight),
            "negative": bool(negative),
        }

        if bounds is not None:
            lower_bound = float(bounds[0])
            upper_bound = float(bounds[1])
            if upper_bound <= lower_bound:
                raise ValueError(f"{name}: upper_bound должен быть больше lower_bound")
            entry["bounds"] = (lower_bound, upper_bound)

        if reference is not None:
            ref_arr = self._broadcast_reference(name, y_arr, reference)
            entry["reference"] = ref_arr
            entry["ratios"] = self._compute_ratios(y_arr, ref_arr, bool(negative))

        self._criteria[str(name)] = entry
        return self

    @staticmethod
    def _broadcast_reference(
        name: str,
        values: np.ndarray,
        reference: Number | Sequence[Number],
    ) -> np.ndarray:
        """Приводит reference к массиву длины values (скаляр — одинаково на все замеры)."""
        if isinstance(reference, (int, float)):
            return np.full(values.shape, float(reference), dtype=float)
        ref = np.array([float(r) for r in reference], dtype=float)
        if ref.size != values.size:
            raise ValueError(f"{name}: reference должен быть скаляром или длины values")
        return ref

    def _compute_ratios(
        self,
        values: np.ndarray,
        reference: np.ndarray,
        negative: bool,
    ) -> np.ndarray:
        """Отношение к эталону по каждому замеру: positive value/ref, negative ref/value."""
        eps = float(self._epsilon)
        v = np.clip(values, eps, None)
        r = np.clip(reference, eps, None)
        return r / v if negative else v / r

    def ratio_index(self, scale: Number = 100.0, cap: Number = 1000.0) -> dict[str, Any]:
        """
        Итоговый индекс как взвешенное геом-среднее отношений к эталону.

        Считается только по критериям, добавленным с ``reference`` (ratio-режим).
        Для каждого критерия отношение по замерам сводится геом-средним, затем берётся
        взвешенное геом-среднее по критериям и умножается на ``scale``:

            index = exp( Σ wᵢ·ln(ratioᵢ) / Σ wᵢ ) * scale

        На эталоне все ``ratio = 1`` -> ``index = scale``. Магнитуда честная: метрика
        ×N даёт ``ratioᵢ = N``, а вклад в индекс — по весу. Границы и power не нужны.

        Args:
            scale (Number): Множитель индекса (на эталоне индекс равен ``scale``).
            cap (Number): Ограничение выброса: ``ratio`` зажимается в ``[1/cap, cap]``,
                чтобы один аномальный замер не перекосил индекс.

        Returns:
            dict[str, Any]: ``index`` и детализация ``criteria``. По каждому критерию:
            ``baseline`` (эталон, геом-среднее замеров эталона), ``result`` (факт. результат,
            геом-среднее замеров прогона), ``ratio`` (индекс критерия = result/baseline для
            positive и baseline/result для negative) и ``weight``. Удобно для UnixBench-таблицы
            ``BASELINE | RESULT | INDEX``.
        """
        ratio_criteria = {n: c for n, c in self._criteria.items() if "ratios" in c}
        if not ratio_criteria:
            raise ValueError("Нет критериев с reference (ratio-режим)")

        cap_value = float(cap)
        if cap_value <= 1.0:
            raise ValueError("cap должен быть > 1")
        total_weight = sum(float(c["weight"]) for c in ratio_criteria.values())
        if total_weight <= 0.0:
            raise ValueError("Сумма весов критериев должна быть > 0")

        eps = float(self._epsilon)
        log_sum = 0.0
        details: dict[str, Any] = {}
        for name, criterion in ratio_criteria.items():
            clamped = np.clip(np.array(criterion["ratios"], dtype=float), 1.0 / cap_value, cap_value)
            crit_ratio = float(np.exp(np.mean(np.log(clamped))))
            weight = float(criterion["weight"])
            values = np.clip(np.array(criterion["values"], dtype=float), eps, None)
            reference = np.clip(np.array(criterion["reference"], dtype=float), eps, None)
            result = float(np.exp(np.mean(np.log(values))))       # факт. результат (геом-среднее)
            baseline = float(np.exp(np.mean(np.log(reference))))  # эталон (геом-среднее)
            log_sum += weight * math.log(crit_ratio)
            details[name] = {
                "baseline": baseline,
                "result": result,
                "ratio": crit_ratio,
                "weight": weight,
            }

        index = float(math.exp(log_sum / total_weight) * float(scale))
        return {"index": index, "scale": float(scale), "criteria": details}

    def test_power(
        self,
        power: Number,
    ) -> None:
        """
        Строит тестовую +/- таблицу рейтингов от уже добавленных критериев.

        Args:
            power (Number): Коэффициент степенного преобразования.

        Returns:
            None: Метод печатает таблицу с колонками ``Dataset``, ``Total rating`` и ``ratio``.
        """
        if not self._criteria:
            raise ValueError("Не добавлено ни одного критерия")

        resolved_power = float(power)
        factor_values = [float(value) for value in TEST_POWER_TABLE_FACTORS]

        eps = 1e-12
        lower_factors = [float(value) for value in factor_values if float(value) < 1.0 - eps]
        upper_factors = [float(value) for value in factor_values if float(value) > 1.0 + eps]
        one_count = sum(1 for value in factor_values if abs(float(value) - 1.0) <= eps)
        if one_count != 1:
            raise ValueError("factors должен содержать ровно одно значение 1.0")
        ordered_factors = sorted(lower_factors) + [1.0] + sorted(upper_factors)

        base_result = self._evaluate_group(resolved_power)
        base_rating = float(base_result["total_rating"])

        table_rows: list[dict[str, Any]] = []

        for index, factor in enumerate(ordered_factors, start=1):
            if abs(float(factor) - 1.0) <= eps:
                table_rows.append(
                    {
                        "Dataset": f"ds_{index}(1) base",
                        "Total rating": float(base_rating),
                        "ratio": 1.0,
                    }
                )
                continue

            synthetic_values_by_name: dict[str, np.ndarray] = {}

            for name, criterion in self._criteria.items():
                base_values = np.array(criterion["values"], dtype=float)
                lower_bound = float(criterion["bounds"][0])
                upper_bound = float(criterion["bounds"][1])
                interval = float(upper_bound - lower_bound)
                margin = interval * float(self._epsilon)
                lower_clip = float(lower_bound + margin)
                upper_clip = float(upper_bound - margin)
                if upper_clip <= lower_clip:
                    lower_clip = float(lower_bound)
                    upper_clip = float(upper_bound)

                if bool(criterion["negative"]):
                    synthetic_raw = base_values / float(factor)
                else:
                    synthetic_raw = base_values * float(factor)

                synthetic_values_by_name[name] = np.clip(synthetic_raw, lower_clip, upper_clip)

            row_result = self._evaluate_group(
                resolved_power,
                synthetic_values_by_name=synthetic_values_by_name,
            )
            total_rating = float(row_result["total_rating"])
            ratio = float(total_rating / base_rating) if base_rating != 0.0 else 0.0

            if ratio > 1.0:
                state = "better"
            elif ratio < 1.0:
                state = "worse"
            else:
                state = "equal"
            dataset_name = f"ds_{index}({factor:g}) {state}"

            table_rows.append(
                {
                    "Dataset": dataset_name,
                    "Total rating": float(total_rating),
                    "ratio": float(ratio),
                }
            )

        print(
            f"\n=== test_power; power={resolved_power:.6f}; "
            f"datasets={len(table_rows)} (base + {len(ordered_factors) - 1} synthetic) ==="
        )
        print(f"{'Dataset':<24} {'Total rating':>14} {'ratio':>10}")
        print("-" * 52)
        for row in table_rows:
            print(
                f"{row['Dataset']:<24} "
                f"{float(row['Total rating']):>14.6f} "
                f"{float(row['ratio']):>10.3f}"
            )

    def compare_power_table(
        self,
        power: Number,
        factors: Sequence[Number] = TEST_POWER_TABLE_FACTORS,
        clip_to_bounds: bool = False,
        print_table: bool = True,
    ) -> dict[str, Any]:
        """
        Строит таблицу сравнения рейтинга при multiplicative-изменении критериев.

        Для каждого коэффициента:
        - positive-критерии умножаются на коэффициент;
        - negative-критерии делятся на коэффициент.

        Args:
            power (Number): Коэффициент степенного преобразования для ``total_rating``.
            factors (Sequence[Number], optional): Коэффициенты изменения критериев.
            clip_to_bounds (bool, optional): Ограничивать synthetic-значения границами bounds.
                По умолчанию отключено, чтобы таблица показывала чистое изменение критериев.
            print_table (bool, optional): Печатать таблицу в stdout.

        Returns:
            dict[str, Any]: Базовый рейтинг и строки таблицы.
        """
        if not self._criteria:
            raise ValueError("Не добавлено ни одного критерия")

        resolved_power = float(power)
        ordered_factors = self._prepare_factor_values(factors)
        base_result = self._evaluate_group(resolved_power)
        base_rating = float(base_result["total_rating"])
        rows: list[dict[str, Any]] = []

        for factor in ordered_factors:
            if math.isclose(float(factor), 1.0, rel_tol=0.0, abs_tol=1e-12):
                rating = float(base_rating)
            else:
                synthetic_values_by_name = self._build_multiplicative_values(
                    float(factor),
                    clip_to_bounds=bool(clip_to_bounds),
                )
                rating = float(
                    self._evaluate_group(
                        resolved_power,
                        synthetic_values_by_name=synthetic_values_by_name,
                    )["total_rating"]
                )

            rating_ratio = float(rating / base_rating) if base_rating != 0.0 else 0.0
            if rating_ratio > 1.0:
                comparison = f"лучше x{rating_ratio:.4f}"
            elif rating_ratio < 1.0 and rating_ratio > 0.0:
                comparison = f"хуже x{1.0 / rating_ratio:.4f}"
            elif math.isclose(rating_ratio, 1.0, rel_tol=0.0, abs_tol=1e-12):
                comparison = "так же x1.0000"
            else:
                comparison = "нельзя сравнить"

            rows.append(
                {
                    "factor": float(factor),
                    "rating": float(rating),
                    "rating_ratio": float(rating_ratio),
                    "comparison": comparison,
                }
            )

        result = {
            "power": float(resolved_power),
            "base_rating": float(base_rating),
            "factors": [float(value) for value in ordered_factors],
            "rows": rows,
        }

        if print_table:
            print("=== compare_power_table ===")
            print(f"power: {resolved_power}")
            print(f"base_rating: {base_rating}")
            print()
            print(f"{'коэф. критериев':>16} | {'полученный рейтинг':>20} | {'относительно оригинала':>24}")
            print("-" * 67)
            for row in rows:
                print(
                    f"{float(row['factor']):>16g} | "
                    f"{float(row['rating']):>20.6f} | "
                    f"{row['comparison']:>24}"
                )

        return result

    def total_rating(
        self,
        power: Number | None = None,
        *,
        scale: Number = 100.0,
        cap: Number = 1000.0,
    ) -> "RatingResult":
        """
        Рассчитывает итоговый рейтинг по всем ранее добавленным критериям.

        Поведение зависит от режима модели (``type`` в конструкторе):
        - ``odds`` (по умолчанию): нужен ``power`` (как раньше). Возвращает odds-рейтинг.
        - ``ratio``: ``power`` не нужен. Возвращает взвешенное геом-среднее отношений к
          эталону × ``scale`` (на эталоне = ``scale``). ``cap`` ограничивает выбросы.

        Args:
            power (Number | None): степень для odds-режима (в ratio-режиме игнорируется).
            scale (Number): множитель индекса для ratio-режима (на эталоне индекс = scale).
            cap (Number): ограничение выброса ratio в ``[1/cap, cap]`` (ratio-режим).

        Returns:
            RatingResult: распаковывается как ``total, criteria = total_rating(...)``.
            ``total`` — итоговый рейтинг, ``criteria`` — детализация по критериям.
        """
        if not self._criteria:
            raise ValueError("Не добавлено ни одного критерия")

        if self._mode == "ratio":
            res = self.ratio_index(scale=scale, cap=cap)
            return RatingResult(float(res["index"]), res["criteria"])

        if power is None:
            raise ValueError("в режиме type='odds' нужно передать power")
        group = self._evaluate_group(float(power))
        return RatingResult(float(group["total_rating"]), group["criteria"])

    def calc_power(
        self,
        sample_count: int = 200,
        random_seed: int = 42,
        bounds_margin: float = 0.01,
        signed_ratio_range: tuple[float, float] = (-4.0, 4.0),
        out_of_band_weight: float = 0.25,
        jitter: float = 0.0,
        trend_strength: float = 0.0,
        power_range: tuple[float, float] = (0.001, 10.0),
        search_iterations: int = 100,
        clip_to_bounds: bool = False,
        test_factors: Sequence[Number] = TEST_POWER_TABLE_FACTORS,
    ) -> dict[str, Any]:
        """
        Отладочная функция подбора ``power`` по multiplicative-наборам от baseline.

        Логика подбора:
            1. Переданные критерии принимаются за baseline с коэффициентом ``1.0``.
            2. Пользователь задаёт рабочий диапазон в signed-виде, например:
               ``(-4, 4)`` -> ``[0.25; 4.0]``,
               ``(-6, 2)`` -> ``[1/6; 2.0]``.
            3. Строится набор коэффициентов из логарифмической сетки диапазона и
               ``TEST_POWER_TABLE_FACTORS``. Базовый коэффициент ``1.0`` исключается
               из подбора и считается отдельной опорной точкой.
            4. Для каждого коэффициента значения критериев меняются multiplicative-образом:
               positive-критерии умножаются на коэффициент, negative-критерии делятся
               на коэффициент.
            5. ``power`` подбирается ternary-search, чтобы отношение
               ``total_rating_synthetic / total_rating_baseline`` было максимально близко
               к коэффициенту изменения критериев.

        Args:
            sample_count (int, optional): Количество synthetic-наборов в диапазоне,
                не считая baseline. Используется вместе с ``test_factors``.
            random_seed (int, optional): Оставлен для совместимости с прежней сигнатурой.
            bounds_margin (float, optional): Оставлен для совместимости с прежней сигнатурой.
            signed_ratio_range (tuple[float, float], optional): Рабочий диапазон относительно
                baseline в signed-виде. Положительное значение означает улучшение во столько раз,
                отрицательное — ухудшение во столько раз. Например ``(-4, 4)`` означает диапазон
                ``[0.25; 4.0]``.
            out_of_band_weight (float, optional): Оставлен для совместимости с прежней сигнатурой.
            jitter (float, optional): Оставлен для совместимости с прежней сигнатурой.
            trend_strength (float, optional): Оставлен для совместимости с прежней сигнатурой.
            power_range (tuple[float, float], optional): Диапазон поиска ``power``.
            search_iterations (int, optional): Количество итераций ternary-search.
            clip_to_bounds (bool, optional): Ограничивать synthetic-значения границами bounds.
                По умолчанию отключено, чтобы коэффициент отражал чистое изменение критериев.
            test_factors (Sequence[Number], optional): Дополнительные коэффициенты проверки.

        Returns:
            dict[str, Any]: Отладочная информация по подобранному ``power``.
        """
        if not self._criteria:
            raise ValueError("Не добавлено ни одного критерия")
        if sample_count < 2:
            raise ValueError("sample_count должен быть >= 2")
        if search_iterations < 1:
            raise ValueError("search_iterations должен быть >= 1")

        power_min = float(power_range[0])
        power_max = float(power_range[1])
        if power_min <= 0.0 or power_max <= 0.0 or power_min >= power_max:
            raise ValueError("power_range должен задавать корректный положительный диапазон")

        ratio_min, ratio_max = self._resolve_signed_ratio_range(signed_ratio_range)
        multipliers = self._build_calc_power_factors(
            ratio_min=float(ratio_min),
            ratio_max=float(ratio_max),
            sample_count=int(sample_count),
            test_factors=test_factors,
        )

        params_by_multiplier: dict[float, list[tuple[float, float]]] = {
            1.0: self._rating_power_params(self._evaluate_group(1.0)),
        }
        for multiplier in multipliers:
            synthetic_values_by_name = self._build_multiplicative_values(
                float(multiplier),
                clip_to_bounds=bool(clip_to_bounds),
            )
            params_by_multiplier[float(multiplier)] = self._rating_power_params(
                self._evaluate_group(
                    1.0,
                    synthetic_values_by_name=synthetic_values_by_name,
                )
            )

        def rating_from_params(power: float, multiplier: float) -> float:
            return sum(
                float(coef) * (float(odds) ** float(power))
                for coef, odds in params_by_multiplier[float(multiplier)]
            )

        def score(power: float) -> float:
            base_rating = rating_from_params(float(power), 1.0)
            if not math.isfinite(base_rating) or base_rating <= 0.0:
                return float("inf")

            errors: list[float] = []
            for multiplier in multipliers:
                sample_rating = rating_from_params(float(power), float(multiplier))
                if not math.isfinite(sample_rating) or sample_rating <= 0.0:
                    return float("inf")
                rating_ratio = float(sample_rating / base_rating)
                errors.append((math.log(rating_ratio) - math.log(float(multiplier))) ** 2)
            return float(sum(errors) / len(errors))

        left = float(power_min)
        right = float(power_max)
        for _ in range(int(search_iterations)):
            left_mid = left + (right - left) / 3.0
            right_mid = right - (right - left) / 3.0
            if score(left_mid) < score(right_mid):
                right = right_mid
            else:
                left = left_mid

        best_power = float((left + right) / 2.0)
        best_score = float(score(best_power))
        base_rating = float(rating_from_params(best_power, 1.0))
        per_sample: list[dict[str, float]] = []
        raw_error_sum = 0.0

        for multiplier in multipliers:
            sample_rating = float(rating_from_params(best_power, float(multiplier)))
            rating_ratio = float(sample_rating / base_rating)
            abs_log_error = abs(math.log(rating_ratio) - math.log(float(multiplier)))
            raw_error_sum += abs_log_error
            per_sample.append(
                {
                    "multiplier": float(multiplier),
                    "target_ratio": float(multiplier),
                    "rating_ratio": float(rating_ratio),
                    "abs_log_error": float(abs_log_error),
                }
            )

        actual_result = self._evaluate_group(float(best_power))
        result = {
            "debug": True,
            "message": (
                "Это отладочная функция для расчета коэффициента степенного преобразования "
                "по multiplicative synthetic-наборам от baseline. "
                "Positive-критерии умножаются на коэффициент, negative-критерии делятся "
                "на коэффициент, а отношение рейтингов подгоняется к этому коэффициенту. "
                "Подобранное значение не сохраняется автоматически: "
                "передайте result['power'] в total_rating(power=...)."
            ),
            "power": float(best_power),
            "mean_abs_log_error": float(raw_error_sum / len(per_sample)),
            "selection_score": float(best_score),
            "sample_count": int(sample_count),
            "random_seed": int(random_seed),
            "bounds_margin": float(bounds_margin),
            "signed_ratio_range": [float(signed_ratio_range[0]), float(signed_ratio_range[1])],
            "focus_ratio_min": float(ratio_min),
            "focus_ratio_max": float(ratio_max),
            "out_of_band_weight": float(out_of_band_weight),
            "jitter": float(jitter),
            "trend_strength": float(trend_strength),
            "power_min": float(power_min),
            "power_max": float(power_max),
            "search_iterations": int(search_iterations),
            "clip_to_bounds": bool(clip_to_bounds),
            "test_factors": [float(value) for value in self._prepare_factor_values(test_factors)],
            "actual_dataset": {
                "rating": float(actual_result["total_rating"]),
                "ratio_to_baseline": 1.0,
            },
            "search_trace": [
                {
                    "stage": "ternary-search",
                    "range": [float(power_min), float(power_max)],
                    "iteration_count": int(search_iterations),
                    "best_power": float(best_power),
                    "selection_score": float(best_score),
                }
            ],
            "synthetic_samples": per_sample,
        }

        print(
            "[MathModel] calc_power(...) — отладочная функция. "
            "Отключите её в release и передавайте зафиксированный power в total_rating(power=...)."
        )
        print(result)
        return result

    def _prepare_factor_values(
        self,
        factors: Sequence[Number],
    ) -> list[float]:
        if not factors:
            raise ValueError("factors не должен быть пустым")

        unique_values: dict[float, float] = {}
        for factor in factors:
            value = float(factor)
            if value <= 0.0:
                raise ValueError("Все factors должны быть > 0")
            unique_values[round(value, 12)] = value

        unique_values[1.0] = 1.0
        values = list(unique_values.values())
        lower_factors = [value for value in values if value < 1.0 - 1e-12]
        upper_factors = [value for value in values if value > 1.0 + 1e-12]
        return sorted(lower_factors) + [1.0] + sorted(upper_factors)

    def _build_calc_power_factors(
        self,
        ratio_min: float,
        ratio_max: float,
        sample_count: int,
        test_factors: Sequence[Number],
    ) -> list[float]:
        grid_factors = self._build_ratio_factor_grid(
            ratio_min=float(ratio_min),
            ratio_max=float(ratio_max),
            sample_count=int(sample_count),
        )
        prepared_test_factors = self._prepare_factor_values(test_factors)

        factors_by_key: dict[float, float] = {}
        for factor in [*grid_factors, *prepared_test_factors]:
            factor = float(factor)
            if (
                float(ratio_min) <= factor <= float(ratio_max)
                and not math.isclose(factor, 1.0, rel_tol=0.0, abs_tol=1e-12)
            ):
                factors_by_key[round(factor, 12)] = factor

        if not factors_by_key:
            raise ValueError("Не удалось построить набор коэффициентов для calc_power")

        return sorted(factors_by_key.values())

    def _rating_power_params(
        self,
        result: dict[str, Any],
    ) -> list[tuple[float, float]]:
        return [
            (
                float(criterion["weight"]) * float(criterion["interval"]),
                float(criterion["odds"]),
            )
            for criterion in result["criteria"].values()
        ]

    def _build_multiplicative_values(
        self,
        multiplier: float,
        clip_to_bounds: bool,
    ) -> dict[str, np.ndarray]:
        if float(multiplier) <= 0.0:
            raise ValueError("multiplier должен быть > 0")

        synthetic_values_by_name: dict[str, np.ndarray] = {}
        for name, criterion in self._criteria.items():
            base_values = np.array(criterion["values"], dtype=float)
            if bool(criterion["negative"]):
                synthetic_raw = base_values / float(multiplier)
            else:
                synthetic_raw = base_values * float(multiplier)

            if bool(clip_to_bounds):
                lower_bound = float(criterion["bounds"][0])
                upper_bound = float(criterion["bounds"][1])
                interval = float(upper_bound - lower_bound)
                margin = interval * float(self._epsilon)
                lower_clip = float(lower_bound + margin)
                upper_clip = float(upper_bound - margin)
                if upper_clip <= lower_clip:
                    lower_clip = float(lower_bound)
                    upper_clip = float(upper_bound)
                synthetic_raw = np.clip(synthetic_raw, lower_clip, upper_clip)

            synthetic_values_by_name[name] = synthetic_raw

        return synthetic_values_by_name

    def _build_random_bounds_samples(
        self,
        sample_count: int,
        random_seed: int,
        bounds_margin: float,
        focus_ratio_min: float,
        focus_ratio_max: float,
        jitter: float,
        trend_strength: float,
    ) -> list[dict[str, Any]]:
        rng = np.random.default_rng(int(random_seed))
        samples: list[dict[str, Any]] = []
        total_weight = sum(float(criterion["weight"]) for criterion in self._criteria.values())
        if total_weight <= 0.0:
            raise ValueError("Сумма весов критериев должна быть > 0")

        target_factors = self._build_ratio_factor_grid(
            float(focus_ratio_min),
            float(focus_ratio_max),
            int(sample_count),
        )

        for sample_index, target_factor in enumerate(target_factors):
            synthetic_values_by_name: dict[str, np.ndarray] = {}
            criterion_ratios: list[tuple[float, float]] = []
            target_factor = float(target_factor)

            for name, criterion in self._criteria.items():
                lower_bound = float(criterion["bounds"][0])
                upper_bound = float(criterion["bounds"][1])
                interval = upper_bound - lower_bound
                z_min = float(bounds_margin)
                z_max = 1.0 - float(bounds_margin)
                base_values = np.array(criterion["values"], dtype=float)

                # Критерии с нулевым или практически нулевым baseline не несут информации
                # для multiplicative-калибровки growth/decay. Для таких критериев оставляем
                # synthetic-профиль неизменным и считаем их нейтральными (ratio = 1).
                if np.max(np.abs(base_values)) <= float(self._epsilon):
                    synthetic_values_by_name[name] = base_values.copy()
                    criterion_ratios.append((float(criterion["weight"]), 1.0))
                    continue

                point_noise = (
                    rng.uniform(-float(jitter), float(jitter), size=base_values.size)
                    if float(jitter) > 0.0
                    else np.zeros(base_values.size, dtype=float)
                )
                trend = (
                    np.linspace(-float(trend_strength), float(trend_strength), base_values.size)
                    if float(trend_strength) > 0.0
                    else np.zeros(base_values.size, dtype=float)
                )
                per_point_factor = target_factor * (1.0 + point_noise + trend)
                per_point_factor = np.clip(per_point_factor, self._epsilon, None)
                geometric_mean = float(np.exp(np.mean(np.log(per_point_factor))))
                if geometric_mean > 0.0:
                    per_point_factor *= target_factor / geometric_mean

                if bool(criterion["negative"]):
                    synthetic_raw = base_values / per_point_factor
                else:
                    synthetic_raw = base_values * per_point_factor

                z_values = self._step_4_minmax(synthetic_raw, lower_bound, upper_bound)
                z_values = np.clip(z_values, z_min, z_max)
                synthetic_raw = lower_bound + z_values * interval
                synthetic_values_by_name[name] = synthetic_raw

                if bool(criterion["negative"]):
                    point_ratios = base_values / np.clip(synthetic_raw, self._epsilon, None)
                else:
                    point_ratios = synthetic_raw / np.clip(base_values, self._epsilon, None)

                point_ratios = np.clip(point_ratios, self._epsilon, None)
                criterion_ratio = float(np.exp(np.mean(np.log(point_ratios))))

                if not math.isfinite(criterion_ratio) or criterion_ratio <= 0.0:
                    criterion_ratio = 1.0
                criterion_ratios.append((float(criterion["weight"]), criterion_ratio))

            weighted_log_ratio = sum(
                float(weight) * math.log(float(ratio))
                for weight, ratio in criterion_ratios
            ) / float(total_weight)
            actual_ratio = float(math.exp(weighted_log_ratio))
            ratio_distortion = abs(math.log(actual_ratio) - math.log(target_factor))
            samples.append(
                {
                    "sample_index": int(sample_index),
                    "requested_factor": float(target_factor),
                    "target_ratio": float(target_factor),
                    "actual_ratio": float(actual_ratio),
                    "ratio_distortion": float(ratio_distortion),
                    "synthetic_values_by_name": synthetic_values_by_name,
                }
            )

        return samples

    def _build_ratio_factor_grid(
        self,
        ratio_min: float,
        ratio_max: float,
        sample_count: int,
    ) -> list[float]:
        if sample_count < 1:
            raise ValueError("sample_count должен быть >= 1")
        if ratio_min <= 0.0 or ratio_max <= 0.0 or ratio_min >= ratio_max:
            raise ValueError("ratio_min и ratio_max должны задавать корректный диапазон")

        extra_points = 1 if ratio_min < 1.0 < ratio_max else 0
        grid_size = int(sample_count) + extra_points
        while True:
            values = np.geomspace(float(ratio_min), float(ratio_max), num=grid_size)
            filtered = [float(value) for value in values if abs(float(value) - 1.0) > 1e-12]
            if len(filtered) >= int(sample_count):
                if len(filtered) > int(sample_count):
                    filtered = sorted(filtered, key=lambda value: abs(math.log(value)))
                    filtered = filtered[: int(sample_count)]
                    filtered.sort()
                return filtered
            grid_size += 1

    def _resolve_signed_ratio_range(
        self,
        signed_ratio_range: tuple[float, float],
    ) -> tuple[float, float]:
        if len(signed_ratio_range) != 2:
            raise ValueError("signed_ratio_range должен содержать ровно 2 значения")

        resolved: list[float] = []
        for value in signed_ratio_range:
            signed_value = float(value)
            if abs(signed_value) < self._epsilon:
                raise ValueError("signed_ratio_range не должен содержать 0")
            if signed_value > 0.0:
                resolved.append(signed_value)
            else:
                resolved.append(1.0 / abs(signed_value))

        ratio_min = min(resolved)
        ratio_max = max(resolved)
        if ratio_min <= 0.0 or ratio_max <= 0.0 or ratio_min >= ratio_max:
            raise ValueError("Не удалось корректно разрешить signed_ratio_range")
        return float(ratio_min), float(ratio_max)

    def _evaluate_power_candidates_random_bounds(
        self,
        candidate_powers: Sequence[float],
        synthetic_samples: Sequence[dict[str, Any]],
        focus_ratio_min: float,
        focus_ratio_max: float,
        out_of_band_weight: float,
    ) -> dict[str, Any]:
        best_result: dict[str, Any] | None = None
        best_score = float("inf")
        scores_by_power: dict[float, float] = {}

        for candidate_power in candidate_powers:
            base_row = self._evaluate_group(float(candidate_power))
            base_rating = float(base_row["total_rating"])
            if not math.isfinite(base_rating) or base_rating <= 0.0:
                continue

            weighted_error_sum = 0.0
            total_weight = 0.0
            raw_error_sum = 0.0
            rows: list[dict[str, float]] = []
            valid_candidate = True

            for sample in synthetic_samples:
                sample_row = self._evaluate_group(
                    float(candidate_power),
                    synthetic_values_by_name=sample["synthetic_values_by_name"],
                )
                sample_rating = float(sample_row["total_rating"])
                if not math.isfinite(sample_rating) or sample_rating <= 0.0:
                    valid_candidate = False
                    break

                target_ratio = float(sample["target_ratio"])
                actual_ratio = float(sample.get("actual_ratio", target_ratio))
                ratio_distortion = float(sample.get("ratio_distortion", 0.0))
                rating_ratio = sample_rating / base_rating
                abs_log_error = abs(math.log(rating_ratio) - math.log(target_ratio))
                in_focus_band = float(focus_ratio_min) <= target_ratio <= float(focus_ratio_max)
                sample_weight = 1.0 if in_focus_band else float(out_of_band_weight)
                if ratio_distortion > 0.0:
                    distortion_weight = 1.0 / (1.0 + 4.0 * ratio_distortion)
                    sample_weight *= max(0.1, distortion_weight)

                weighted_error_sum += sample_weight * abs_log_error
                total_weight += sample_weight
                raw_error_sum += abs_log_error
                rows.append(
                    {
                        "sample_index": float(sample["sample_index"]),
                        "requested_factor": float(sample.get("requested_factor", target_ratio)),
                        "target_ratio": float(target_ratio),
                        "actual_ratio": float(actual_ratio),
                        "rating_ratio": float(rating_ratio),
                        "ratio_distortion": float(ratio_distortion),
                        "abs_log_error": float(abs_log_error),
                        "sample_weight": float(sample_weight),
                        "in_focus_band": 1.0 if in_focus_band else 0.0,
                    }
                )

            if not valid_candidate or total_weight <= 0.0 or not rows:
                continue

            mean_abs_log_error = raw_error_sum / float(len(rows))
            selection_score = weighted_error_sum / float(total_weight)
            scores_by_power[float(candidate_power)] = float(selection_score)

            if selection_score < best_score:
                best_score = selection_score
                best_result = {
                    "power": float(candidate_power),
                    "mean_abs_log_error": float(mean_abs_log_error),
                    "selection_score": float(selection_score),
                    "per_sample": rows,
                }

        if best_result is None:
            raise ValueError("Не удалось подобрать power по random-bounds synthetic-наборам")

        best_result["scores_by_power"] = scores_by_power
        return best_result

    def _select_zoom_interval(
        self,
        stage_values: Sequence[float],
        scores_by_power: dict[float, float],
        best_power: float,
    ) -> tuple[float, float]:
        ordered_values = sorted(float(value) for value in stage_values)
        best_index = ordered_values.index(float(best_power))

        if len(ordered_values) == 1:
            return float(ordered_values[0]), float(ordered_values[0])
        if best_index == 0:
            return float(ordered_values[0]), float(ordered_values[1])
        if best_index == len(ordered_values) - 1:
            return float(ordered_values[-2]), float(ordered_values[-1])

        left_value = float(ordered_values[best_index - 1])
        right_value = float(ordered_values[best_index + 1])
        left_score = float(scores_by_power[left_value])
        right_score = float(scores_by_power[right_value])

        if left_score <= right_score:
            return left_value, float(best_power)
        return float(best_power), right_value

    def _build_power_range(
        self,
        left_bound: float,
        right_bound: float,
        step: float,
    ) -> list[float]:
        if step <= 0.0:
            raise ValueError("step должен быть > 0")

        scale = int(round(1.0 / float(step)))
        start = int(round(float(left_bound) * scale))
        stop = int(round(float(right_bound) * scale))
        values = [index / float(scale) for index in range(start, stop + 1)]

        if not values:
            return [float(left_bound), float(right_bound)]
        if float(values[0]) != float(left_bound):
            values.insert(0, float(left_bound))
        if float(values[-1]) != float(right_bound):
            values.append(float(right_bound))
        return [float(value) for value in values]

    def _build_synthetic_values(
        self,
        criterion: dict[str, Any],
        synthetic_level: float,
    ) -> np.ndarray:
        lower_bound = float(criterion["bounds"][0])
        upper_bound = float(criterion["bounds"][1])
        is_negative = bool(criterion["negative"])
        interval = upper_bound - lower_bound

        if is_negative:
            z_level = 1.0 - float(synthetic_level)
        else:
            z_level = float(synthetic_level)

        raw_value = lower_bound + z_level * interval
        return np.full_like(np.array(criterion["values"], dtype=float), raw_value, dtype=float)

    def _build_synthetic_profile_values(
        self,
        criterion: dict[str, Any],
        synthetic_level: float,
    ) -> np.ndarray:
        criterion_name = str(criterion["name"])
        lower_bound = float(criterion["bounds"][0])
        upper_bound = float(criterion["bounds"][1])
        is_negative = bool(criterion["negative"])
        interval = upper_bound - lower_bound
        x_arr = np.array(criterion["iterations"], dtype=float)
        size = int(x_arr.size)

        if size < 2:
            return self._build_synthetic_values(criterion, synthetic_level)

        seed_key = f"{criterion_name}:{float(synthetic_level):.6f}"
        seed = binascii.crc32(seed_key.encode("utf-8")) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)

        t = np.linspace(0.0, 1.0, size)
        centered = t - 0.5
        slope = rng.uniform(-0.10, 0.10)
        curve = rng.uniform(-0.08, 0.08) * ((2.0 * t - 1.0) ** 2 - 0.35)
        noise = rng.normal(0.0, 0.018, size)

        oriented_values = float(synthetic_level) + slope * centered + curve + noise
        oriented_values = np.clip(oriented_values, POWER_SAFE_LEVEL_MIN, POWER_SAFE_LEVEL_MAX)

        if is_negative:
            z_values = 1.0 - oriented_values
        else:
            z_values = oriented_values

        return lower_bound + z_values * interval

    def _prepare_safe_levels(self, synthetic_levels: Sequence[float]) -> list[float]:
        safe_levels = sorted(
            {
                float(np.clip(float(level), POWER_SAFE_LEVEL_MIN, POWER_SAFE_LEVEL_MAX))
                for level in synthetic_levels
            }
        )
        return safe_levels

    def _build_relative_synthetic_group_deterministic(
        self,
        factor: float,
    ) -> dict[str, np.ndarray]:
        synthetic_values_by_name: dict[str, np.ndarray] = {}

        for name, criterion in self._criteria.items():
            base_values = np.array(criterion["values"], dtype=float)
            lower_bound = float(criterion["bounds"][0])
            upper_bound = float(criterion["bounds"][1])
            is_negative = bool(criterion["negative"])

            if is_negative:
                synthetic_raw = base_values / float(factor)
            else:
                synthetic_raw = base_values * float(factor)

            z_values = self._step_4_minmax(synthetic_raw, lower_bound, upper_bound)
            z_values = np.clip(z_values, self._epsilon, 1.0 - self._epsilon)
            synthetic_values_by_name[name] = lower_bound + z_values * (upper_bound - lower_bound)

        return synthetic_values_by_name

    def _build_relative_synthetic_group(
        self,
        factor: float,
        rng: np.random.Generator,
        jitter: float,
        trend_strength: float,
    ) -> dict[str, np.ndarray]:
        synthetic_values_by_name: dict[str, np.ndarray] = {}

        for name, criterion in self._criteria.items():
            base_values = np.array(criterion["values"], dtype=float)
            lower_bound = float(criterion["bounds"][0])
            upper_bound = float(criterion["bounds"][1])
            is_negative = bool(criterion["negative"])

            point_noise = rng.uniform(-float(jitter), float(jitter), size=base_values.size)
            trend = np.linspace(-float(trend_strength), float(trend_strength), base_values.size)
            per_point_factor = float(factor) * (1.0 + point_noise + trend)
            per_point_factor = np.clip(per_point_factor, 0.05, None)

            if is_negative:
                synthetic_raw = base_values / per_point_factor
            else:
                synthetic_raw = base_values * per_point_factor

            z_values = self._step_4_minmax(synthetic_raw, lower_bound, upper_bound)
            z_values = np.clip(z_values, POWER_SAFE_LEVEL_MIN, POWER_SAFE_LEVEL_MAX)
            synthetic_values_by_name[name] = lower_bound + z_values * (upper_bound - lower_bound)

        return synthetic_values_by_name

    def _step_4_minmax(
        self,
        raw_values: np.ndarray,
        lower_bound: float,
        upper_bound: float,
    ) -> np.ndarray:
        return (raw_values - lower_bound) / (upper_bound - lower_bound)

    def _step_5_orient(
        self,
        z_values: np.ndarray,
        is_negative: bool,
    ) -> np.ndarray:
        if is_negative and self._invert_negative:
            return 1.0 - z_values
        return z_values.copy()

    def _step_6_approximate(
        self,
        x_arr: np.ndarray,
        oriented_values: np.ndarray,
    ) -> tuple[np.poly1d, int]:
        degree = int(self._max_degree)
        while degree >= 1:
            with warnings.catch_warnings():
                warnings.filterwarnings("error", category=RankWarning)
                try:
                    return np.poly1d(np.polyfit(x_arr, oriented_values, degree)), degree
                except RankWarning:
                    degree -= 1
        return np.poly1d(np.polyfit(x_arr, oriented_values, 0)), 0

    def _step_7_integral(
        self,
        poly: np.poly1d,
        x_arr: np.ndarray,
    ) -> float:
        antiderivative = np.polyint(poly)
        return float(antiderivative(x_arr[-1]) - antiderivative(x_arr[0]))

    def _step_8_interval(
        self,
        x_arr: np.ndarray,
    ) -> float:
        interval = float(x_arr[-1] - x_arr[0])
        if interval <= 0.0:
            raise ValueError("Длина интервала должна быть > 0")
        return interval

    def _step_9_area_mean(
        self,
        area_raw: float,
        interval: float,
    ) -> float:
        return float(area_raw) / float(interval)

    def _step_10_clamp(
        self,
        area_mean: float,
    ) -> float:
        return float(np.clip(area_mean, self._epsilon, 1.0 - self._epsilon))

    def _step_11_odds(
        self,
        area_clamped: float,
    ) -> float:
        return float(area_clamped) / (1.0 - float(area_clamped))

    def _step_12_transform(
        self,
        odds: float,
        power: float,
        interval: float,
        area_clamped: float,
    ) -> tuple[float, float]:
        area_transformed = (float(odds) ** float(power)) * float(interval)
        return float(area_transformed), float(power)

    def _step_13_weight(
        self,
        weight: float,
        area_transformed: float,
    ) -> float:
        return float(weight) * float(area_transformed)

    def _evaluate_criterion(self, criterion: dict[str, Any], power: float) -> dict[str, Any]:
        x_arr = np.array(criterion["iterations"], dtype=float)
        raw_values = np.array(criterion["values"], dtype=float)
        weight = float(criterion["weight"])
        lower_bound = float(criterion["bounds"][0])
        upper_bound = float(criterion["bounds"][1])
        is_negative = bool(criterion["negative"])

        # Полностью нулевой критерий не содержит информации для относительного сравнения
        # запусков: multiplicative scaling оставляет его нулевым, а odds-преобразование
        # превращает такой критерий в постоянный доминирующий вклад. Для ALT-модели
        # считаем его нейтральным и исключаем из TOTAL_RATING.
        if np.max(np.abs(raw_values)) <= float(self._epsilon):
            return {
                "type": "negative" if is_negative else "positive",
                "weight": float(weight),
                "bounds": [float(lower_bound), float(upper_bound)],
                "power": float(power),
                "effective_power": float(power),
                "iterations": [float(value) for value in x_arr],
                "raw_values": [float(value) for value in raw_values],
                "z_values": [],
                "oriented_values": [],
                "approx_degree": 0,
                "interval": float(x_arr[-1] - x_arr[0]) if x_arr.size > 1 else 0.0,
                "area_raw": 0.0,
                "area_mean": 0.0,
                "area_clamped": 0.0,
                "odds": 0.0,
                "area_transformed": 0.0,
                "contribution": 0.0,
                "skipped_zero_baseline": True,
            }

        z_values = self._step_4_minmax(raw_values, lower_bound, upper_bound)
        oriented_values = self._step_5_orient(z_values, is_negative)
        poly, used_degree = self._step_6_approximate(x_arr, oriented_values)
        area_raw = self._step_7_integral(poly, x_arr)
        interval = self._step_8_interval(x_arr)
        area_mean = self._step_9_area_mean(area_raw, interval)
        area_clamped = self._step_10_clamp(area_mean)
        odds = self._step_11_odds(area_clamped)
        area_transformed, effective_power = self._step_12_transform(
            odds,
            power,
            interval,
            area_clamped,
        )
        contribution = self._step_13_weight(weight, area_transformed)

        return {
            "type": "negative" if is_negative else "positive",
            "weight": float(weight),
            "bounds": [float(lower_bound), float(upper_bound)],
            "power": float(power),
            "effective_power": float(effective_power),
            "iterations": [float(value) for value in x_arr],
            "raw_values": [float(value) for value in raw_values],
            "z_values": [float(value) for value in z_values],
            "oriented_values": [float(value) for value in oriented_values],
            "approx_degree": int(used_degree),
            "interval": float(interval),
            "area_raw": float(area_raw),
            "area_mean": float(area_mean),
            "area_clamped": float(area_clamped),
            "odds": float(odds),
            "area_transformed": float(area_transformed),
            "contribution": float(contribution),
            "skipped_zero_baseline": False,
        }

    def _evaluate_group(
        self,
        power: float,
        *,
        synthetic_level: float | None = None,
        synthetic_profile: bool = False,
        synthetic_values_by_name: dict[str, np.ndarray] | None = None,
    ) -> dict[str, Any]:
        total = 0.0
        details: dict[str, Any] = {}

        for name, criterion in self._criteria.items():
            criterion_for_calc = dict(criterion)
            if synthetic_values_by_name is not None:
                criterion_for_calc["values"] = np.array(synthetic_values_by_name[name], dtype=float)
            elif synthetic_level is not None:
                if synthetic_profile:
                    criterion_for_calc["values"] = self._build_synthetic_profile_values(
                        criterion_for_calc,
                        float(synthetic_level),
                    )
                else:
                    criterion_for_calc["values"] = self._build_synthetic_values(
                        criterion_for_calc,
                        float(synthetic_level),
                    )
            row = self._evaluate_criterion(criterion_for_calc, float(power))
            total += float(row["contribution"])
            details[name] = row

        return {
            "total_rating": float(total),
            "criteria": details,
            "power": float(power),
        }
