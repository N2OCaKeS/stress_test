from __future__ import annotations

import binascii
import math
import warnings
from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.exceptions import RankWarning


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
    """

    def __init__(self) -> None:
        """
        Создаёт пустую математическую модель.

        Внутренние параметры модели фиксированы:
        - максимальная степень полинома: 10
        - инверсия negative-критериев: включена
        - epsilon для clipping: 1e-6
        """
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
        bounds: tuple[Number, Number],
    ) -> "MathModel":
        """
        Добавляет критерий в модель.

        Args:
            name (str): Название критерия.
            iterations (Sequence[str | Number]): Ось итераций для критерия.
            values (Sequence[Number]): Значения критерия на каждой итерации.
            weight (Number): Вес критерия в итоговом рейтинге.
            negative (bool): Признак negative-критерия. ``True`` если меньше = лучше.
            bounds (tuple[Number, Number]): Границы ``(L, U)`` для min-max нормализации.

        Returns:
            MathModel: Текущий экземпляр модели для chaining-вызовов.
        """
        if not name:
            raise ValueError("name не должен быть пустым")
        if len(iterations) != len(values):
            raise ValueError("iterations и values должны быть одинаковой длины")
        if len(iterations) < 2:
            raise ValueError("Для критерия требуется минимум 2 точки")

        x_arr = np.array([float(x) for x in iterations], dtype=float)
        y_arr = np.array([float(v) for v in values], dtype=float)
        lower_bound = float(bounds[0])
        upper_bound = float(bounds[1])

        if upper_bound <= lower_bound:
            raise ValueError(f"{name}: upper_bound должен быть больше lower_bound")
        if float(weight) < 0.0:
            raise ValueError(f"{name}: weight должен быть >= 0")
        if np.any(np.diff(x_arr) <= 0.0):
            raise ValueError(f"{name}: iterations должны быть строго возрастающими")

        self._criteria[str(name)] = {
            "name": str(name),
            "iterations": x_arr,
            "values": y_arr,
            "weight": float(weight),
            "negative": bool(negative),
            "bounds": (lower_bound, upper_bound),
        }
        return self

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

    def total_rating(self, power: Number) -> dict[str, Any]:
        """
        Рассчитывает итоговый рейтинг по всем ранее добавленным критериям.

        Перед вызовом необходимо явно передать общий ``power`` для всей группы критериев.
        Обычно этот коэффициент заранее подбирается через ``calc_power(...)``
        и затем фиксируется в коде теста для повторного использования между запусками.

        Args:
            power (Number): Зафиксированный коэффициент степенного преобразования.

        Returns:
            dict[str, Any]: Итоговый рейтинг и детализация по критериям.
        """
        if not self._criteria:
            raise ValueError("Не добавлено ни одного критерия")
        return self._evaluate_group(float(power))

    def calc_power(
        self,
        sample_count: int = 50,
        random_seed: int = 42,
        bounds_margin: float = 0.01,
        signed_ratio_range: tuple[float, float] = (-4.0, 4.0),
        out_of_band_weight: float = 0.25,
        jitter: float = 0.0,
        trend_strength: float = 0.0,
    ) -> dict[str, Any]:
        """
        Отладочная функция подбора ``power`` по synthetic-наборам от baseline в заданном диапазоне.

        Логика подбора:
            1. Переданные критерии принимаются за baseline с коэффициентом ``1.0``.
            2. Пользователь задаёт рабочий диапазон в signed-виде, например:
               ``(-4, 4)`` -> ``[0.25; 4.0]``,
               ``(-6, 2)`` -> ``[1/6; 2.0]``.
            3. Строится ``sample_count`` synthetic-наборов от baseline по детерминированной
               логарифмической сетке факторов внутри диапазона. Базовый набор ``1.0``
               не входит в synthetic-сетку и считается отдельной опорной точкой.
            4. Для каждого synthetic-набора значения строятся от baseline; при необходимости
               применяется мягкая защита от выхода за ``bounds``.
            5. ``power`` подбирается прямым brute-force перебором по захардкоженной сетке.
            6. Выбирается такой ``power``, при котором отношение
               ``total_rating_synthetic / total_rating_baseline`` максимально близко
               к реальному коэффициенту synthetic-набора.

        Args:
            sample_count (int, optional): Количество synthetic-наборов в диапазоне,
                не считая baseline.
            random_seed (int, optional): Seed для воспроизводимого точечного разброса,
                если ``jitter`` или ``trend_strength`` больше нуля.
            bounds_margin (float, optional): Защитный отступ от границ bounds.
            signed_ratio_range (tuple[float, float], optional): Рабочий диапазон относительно
                baseline в signed-виде. Положительное значение означает улучшение во столько раз,
                отрицательное — ухудшение во столько раз. Например ``(-4, 4)`` означает диапазон
                ``[0.25; 4.0]``.
            out_of_band_weight (float, optional): Вес ошибок вне приоритетного диапазона.
                Для текущей deterministic-сетки обычно можно оставлять ``1.0`` или значение по
                умолчанию.
            jitter (float, optional): Амплитуда случайного разброса по точкам внутри набора.
                По умолчанию ``0.0`` для максимальной точности.
            trend_strength (float, optional): Сила слабого линейного тренда по итерациям.
                По умолчанию ``0.0`` для максимальной точности.

        Returns:
            dict[str, Any]: Отладочная информация по подобранному ``power``.
        """
        if not self._criteria:
            raise ValueError("Не добавлено ни одного критерия")
        if sample_count < 5:
            raise ValueError("sample_count должен быть >= 5")
        if not 0.0 < bounds_margin < 0.5:
            raise ValueError("bounds_margin должен быть в диапазоне (0, 0.5)")
        if out_of_band_weight <= 0.0:
            raise ValueError("out_of_band_weight должен быть > 0")
        if jitter < 0.0:
            raise ValueError("jitter должен быть >= 0")
        if trend_strength < 0.0:
            raise ValueError("trend_strength должен быть >= 0")

        focus_ratio_min, focus_ratio_max = self._resolve_signed_ratio_range(signed_ratio_range)

        synthetic_samples = self._build_random_bounds_samples(
            sample_count=int(sample_count),
            random_seed=int(random_seed),
            bounds_margin=float(bounds_margin),
            focus_ratio_min=float(focus_ratio_min),
            focus_ratio_max=float(focus_ratio_max),
            jitter=float(jitter),
            trend_strength=float(trend_strength),
        )

        candidate_powers = np.linspace(
            float(RANDOM_BOUNDS_POWER_MIN),
            float(RANDOM_BOUNDS_POWER_MAX),
            num=int(RANDOM_BOUNDS_POWER_STEPS),
            dtype=float,
        )
        best_result = self._evaluate_power_candidates_random_bounds(
            candidate_powers=[float(value) for value in candidate_powers],
            synthetic_samples=synthetic_samples,
            focus_ratio_min=float(focus_ratio_min),
            focus_ratio_max=float(focus_ratio_max),
            out_of_band_weight=float(out_of_band_weight),
        )
        search_trace: list[dict[str, Any]] = [
            {
                "stage": "bruteforce",
                "step": float(candidate_powers[1] - candidate_powers[0]),
                "range": [float(candidate_powers[0]), float(candidate_powers[-1])],
                "candidate_count": int(candidate_powers.size),
                "best_power": float(best_result["power"]),
                "selection_score": float(best_result["selection_score"]),
            }
        ]

        print(
            "[MathModel] calc_power(...) — отладочная функция. "
            "Отключите её в release и передавайте зафиксированный power в total_rating(power=...)."
        )

        actual_result = self._evaluate_group(float(best_result["power"]))
        return {
            "debug": True,
            "message": (
                "Это отладочная функция для расчета коэффициента степенного преобразования "
                "по deterministic synthetic-наборам от baseline в заданном диапазоне. "
                "Подбор выполняется прямым перебором значений power "
                "в захардкоженном интервале brute-force поиска. "
                "Подобранное значение не сохраняется автоматически: "
                "передайте result['power'] в total_rating(power=...)."
            ),
            "power": float(best_result["power"]),
            "mean_abs_log_error": float(best_result["mean_abs_log_error"]),
            "selection_score": float(best_result["selection_score"]),
            "sample_count": int(sample_count),
            "random_seed": int(random_seed),
            "bounds_margin": float(bounds_margin),
            "signed_ratio_range": [float(signed_ratio_range[0]), float(signed_ratio_range[1])],
            "focus_ratio_min": float(focus_ratio_min),
            "focus_ratio_max": float(focus_ratio_max),
            "out_of_band_weight": float(out_of_band_weight),
            "jitter": float(jitter),
            "trend_strength": float(trend_strength),
            "power_min": float(RANDOM_BOUNDS_POWER_MIN),
            "power_max": float(RANDOM_BOUNDS_POWER_MAX),
            "power_steps": int(RANDOM_BOUNDS_POWER_STEPS),
            "actual_dataset": {
                "rating": float(actual_result["total_rating"]),
                "ratio_to_baseline": 1.0,
            },
            "search_trace": search_trace,
            "synthetic_samples": best_result["per_sample"],
        }

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
