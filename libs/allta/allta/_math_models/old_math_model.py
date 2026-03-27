from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.exceptions import RankWarning


Number = float | int
RawMetricValue = Number | Mapping[str, Any]
RawDataset = Mapping[str | Number, Mapping[str, RawMetricValue]]


@dataclass(frozen=True)
class CriterionParams:
    """
    Параметры критерия для old-модели.

    Args:
        weight (float | None, optional): Вес критерия.
        negative (bool | None, optional): Признак negative-критерия.
        bounds (tuple[float, float] | None, optional): Границы нормализации ``(L, U)``.
        max_degree (int | None, optional): Максимальная степень полинома.
    """

    weight: float | None = None
    negative: bool | None = None
    bounds: tuple[float, float] | None = None
    max_degree: int | None = None

    def merged(self, override: "CriterionParams") -> "CriterionParams":
        """
        Объединяет текущие параметры с переопределением.

        Args:
            override (CriterionParams): Параметры, которые перекрывают текущие значения.

        Returns:
            CriterionParams: Новый объект параметров.
        """
        return CriterionParams(
            weight=self.weight if override.weight is None else override.weight,
            negative=self.negative if override.negative is None else override.negative,
            bounds=self.bounds if override.bounds is None else override.bounds,
            max_degree=self.max_degree if override.max_degree is None else override.max_degree,
        )

    @classmethod
    def from_value(cls, value: "CriterionParams | Mapping[str, Any] | None") -> "CriterionParams":
        """
        Строит объект параметров из значения.

        Args:
            value (CriterionParams | Mapping[str, Any] | None): Исходное значение параметров.

        Returns:
            CriterionParams: Нормализованный объект параметров.
        """
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("CriterionParams должен быть CriterionParams или dict-подобным объектом")

        bounds_raw = value.get("bounds")
        bounds: tuple[float, float] | None = None
        if bounds_raw is not None:
            if not isinstance(bounds_raw, Sequence) or len(bounds_raw) != 2:
                raise ValueError("bounds должен быть последовательностью из 2 чисел")
            bounds = (float(bounds_raw[0]), float(bounds_raw[1]))

        return cls(
            weight=float(value["weight"]) if value.get("weight") is not None else None,
            negative=bool(value["negative"]) if value.get("negative") is not None else None,
            bounds=bounds,
            max_degree=int(value["max_degree"]) if value.get("max_degree") is not None else None,
        )


class OldMathModel:
    """
    Старая математическая модель (логика old_math_models.py) в классовом API.

    Шаги расчёта для каждого критерия:
    1. min-max нормализация
    2. полиномиальная аппроксимация
    3. интеграл
    4. умножение на weight
    5. для negative: степень ``-1`` на финальном значении
    """

    def __init__(
        self,
        *,
        max_degree: int = 3,
        normalize_integral: bool = False,
        epsilon: float = 1e-12,
    ) -> None:
        """
        Создаёт пустую old-модель.

        Args:
            max_degree (int, optional): Максимальная степень полинома аппроксимации.
            normalize_integral (bool, optional): Нормализовать интеграл на длину интервала.
            epsilon (float, optional): Число для защиты от деления на ноль.
        """
        self._max_degree = int(max_degree)
        self._normalize_integral = bool(normalize_integral)
        self._epsilon = float(epsilon)
        self._criteria: dict[str, dict[str, Any]] = {}

    def add_criterion(
        self,
        name: str,
        iterations: Sequence[str | Number],
        values: Sequence[Number],
        weight: Number,
        negative: bool,
        bounds: tuple[Number, Number],
        *,
        max_degree: int | None = None,
    ) -> "OldMathModel":
        """
        Добавляет критерий в old-модель.

        Args:
            name (str): Название критерия.
            iterations (Sequence[str | Number]): Значения оси X.
            values (Sequence[Number]): Значения критерия по оси X.
            weight (Number): Вес критерия.
            negative (bool): Признак negative-критерия.
            bounds (tuple[Number, Number]): Границы нормализации ``(L, U)``.
            max_degree (int | None, optional): Локальная степень аппроксимации для критерия.

        Returns:
            OldMathModel: Текущий экземпляр для chaining-вызовов.
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
            "max_degree": int(self._max_degree if max_degree is None else max_degree),
        }
        return self

    def total_rating(self) -> dict[str, Any]:
        """
        Рассчитывает итоговый рейтинг по всем добавленным критериям.

        Returns:
            dict[str, Any]: Итоговый рейтинг и детализация по критериям.
        """
        if not self._criteria:
            raise ValueError("Не добавлено ни одного критерия")

        total = 0.0
        details: dict[str, Any] = {}
        for name, criterion in self._criteria.items():
            row = self._evaluate_criterion(criterion)
            total += float(row["final_contribution"])
            details[name] = row

        return {
            "total_rating": float(total),
            "rating": float(total),
            "criteria": details,
            "integral_normalized": bool(self._normalize_integral),
            "model": "old_math_md_step8_power_minus_one",
        }

    def _evaluate_criterion(self, criterion: Mapping[str, Any]) -> dict[str, Any]:
        name = str(criterion["name"])
        x_arr = np.array(criterion["iterations"], dtype=float)
        raw_values = np.array(criterion["values"], dtype=float)
        weight = float(criterion["weight"])
        is_negative = bool(criterion["negative"])
        lower_bound, upper_bound = criterion["bounds"]
        max_degree = int(criterion["max_degree"])

        z_values = self._step_4_minmax(raw_values, float(lower_bound), float(upper_bound))
        poly, used_degree = self._step_5_approximate(x_arr, z_values, max_degree)
        area_raw = self._step_6_integral(poly, x_arr)
        interval = self._step_7_interval(x_arr)
        area_step6 = float(area_raw) / float(interval) if self._normalize_integral else float(area_raw)

        base_contribution = weight * area_step6
        if is_negative:
            safe_base_contribution = float(base_contribution)
            inverse_guard_applied = False
            if abs(safe_base_contribution) <= float(self._epsilon):
                inverse_guard_applied = True
                safe_base_contribution = (
                    float(self._epsilon) if safe_base_contribution >= 0.0 else -float(self._epsilon)
                )
                warnings.warn(
                    (
                        f"{name}: (weight * integral) близко к 0. "
                        f"Для применения степени -1 используется epsilon={self._epsilon}."
                    ),
                    RuntimeWarning,
                    stacklevel=2,
                )
            value_after_step8 = float(safe_base_contribution) ** -1
            final_contribution = value_after_step8
        else:
            value_after_step8 = float(base_contribution)
            final_contribution = value_after_step8
            safe_base_contribution = float(base_contribution)
            inverse_guard_applied = False

        return {
            "type": "negative" if is_negative else "positive",
            "weight": float(weight),
            "bounds": [float(lower_bound), float(upper_bound)],
            "x_values": [float(value) for value in x_arr.tolist()],
            "raw_values": [float(value) for value in raw_values.tolist()],
            "z_values": [float(value) for value in z_values.tolist()],
            "approx_degree": int(used_degree),
            "interval": float(interval),
            "area_step6": float(area_step6),
            "base_contribution_step7": float(base_contribution),
            "safe_base_contribution_step7": float(safe_base_contribution),
            "inverse_guard_applied": bool(inverse_guard_applied),
            "value_after_step8": float(value_after_step8),
            "final_contribution": float(final_contribution),
        }

    def _step_4_minmax(
        self,
        raw_values: np.ndarray,
        lower_bound: float,
        upper_bound: float,
    ) -> np.ndarray:
        return (raw_values - float(lower_bound)) / (float(upper_bound) - float(lower_bound))

    def _step_5_approximate(
        self,
        x_arr: np.ndarray,
        z_values: np.ndarray,
        max_degree: int,
    ) -> tuple[np.poly1d, int]:
        degree = min(int(max_degree), int(x_arr.size) - 1)
        while degree >= 1:
            with warnings.catch_warnings():
                warnings.filterwarnings("error", category=RankWarning)
                try:
                    return np.poly1d(np.polyfit(x_arr, z_values, degree)), degree
                except RankWarning:
                    degree -= 1
        return np.poly1d(np.polyfit(x_arr, z_values, 0)), 0

    def _step_6_integral(
        self,
        poly: np.poly1d,
        x_arr: np.ndarray,
    ) -> float:
        antiderivative = np.polyint(poly)
        return float(antiderivative(x_arr[-1]) - antiderivative(x_arr[0]))

    def _step_7_interval(self, x_arr: np.ndarray) -> float:
        interval = float(x_arr[-1] - x_arr[0])
        if interval <= 0.0:
            raise ValueError("Длина интервала должна быть > 0")
        return interval



def calculate_old_total_rating(
    dataset: RawDataset,
    negative_metrics: set[str] | Sequence[str] | None = None,
    bounds_by_metric: Mapping[str, tuple[float, float]] | None = None,
    max_degree: int = 3,
    normalize_integral: bool = False,
    epsilon: float = 1e-12,
    criteria_params: Mapping[str, CriterionParams | Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Функциональный wrapper для расчёта old-модели.

    Args:
        dataset (RawDataset): Датасет в формате ``{x_value: {metric_name: value | payload}}``.
        negative_metrics (set[str] | Sequence[str] | None, optional): Список negative-критериев.
        bounds_by_metric (Mapping[str, tuple[float, float]] | None, optional): Границы по критериям.
        max_degree (int, optional): Максимальная степень полинома по умолчанию.
        normalize_integral (bool, optional): Нормализовать интеграл на длину интервала.
        epsilon (float, optional): Число для защиты от деления на ноль.
        criteria_params (Mapping[str, CriterionParams | Mapping[str, Any]] | None, optional):
            Переопределения параметров критериев. Ключ ``"*"`` задаёт общие параметры.

    Returns:
        dict[str, Any]: Итоговый рейтинг и детализация по критериям.
    """
    model = OldMathModel(
        max_degree=max_degree,
        normalize_integral=normalize_integral,
        epsilon=epsilon,
    )

    criteria_params_map: dict[str, CriterionParams] = {}
    if criteria_params:
        for name, params in criteria_params.items():
            criteria_params_map[str(name)] = CriterionParams.from_value(params)

    common_params = criteria_params_map.get("*", CriterionParams())
    negative_set = set(str(name) for name in (negative_metrics or set()))

    grouped: dict[str, dict[str, Any]] = {}
    for iteration_key, metrics in dataset.items():
        iteration = float(iteration_key)
        for metric_name, payload in metrics.items():
            metric_key = str(metric_name)
            grouped.setdefault(metric_key, {"iterations": [], "values": [], "weights": []})
            grouped[metric_key]["iterations"].append(iteration)

            if isinstance(payload, Mapping):
                if "value" not in payload:
                    raise ValueError(f"{metric_key}: для dict-формата обязателен ключ 'value'")
                grouped[metric_key]["values"].append(float(payload["value"]))
                if payload.get("weight") is not None:
                    grouped[metric_key]["weights"].append(float(payload["weight"]))
            else:
                grouped[metric_key]["values"].append(float(payload))

    for metric_name, payload in grouped.items():
        metric_specific = criteria_params_map.get(metric_name, CriterionParams())
        params = common_params.merged(metric_specific)

        iterations = payload["iterations"]
        values = payload["values"]

        weight: float | None = params.weight
        if weight is None:
            if payload["weights"]:
                weight = float(payload["weights"][0])
                if any(abs(float(w) - float(weight)) > 1e-12 for w in payload["weights"][1:]):
                    warnings.warn(
                        (
                            f"несколько весов внутри критерия {metric_name}: {payload['weights']}. "
                            f"Используется первый вес = {weight}."
                        ),
                        stacklevel=2,
                    )
        if weight is None:
            raise ValueError(
                f"{metric_name}: не найден weight. Передайте weight в dataset или в criteria_params"
            )

        if params.bounds is not None:
            bounds = (float(params.bounds[0]), float(params.bounds[1]))
        elif bounds_by_metric and metric_name in bounds_by_metric:
            bounds = (float(bounds_by_metric[metric_name][0]), float(bounds_by_metric[metric_name][1]))
        else:
            bounds = (float(min(values)), float(max(values)))

        negative = bool(params.negative) or (metric_name in negative_set)
        criterion_degree = int(params.max_degree) if params.max_degree is not None else int(max_degree)

        order = np.argsort(np.array(iterations, dtype=float))
        sorted_iterations = [float(np.array(iterations, dtype=float)[idx]) for idx in order]
        sorted_values = [float(np.array(values, dtype=float)[idx]) for idx in order]

        model.add_criterion(
            name=metric_name,
            iterations=sorted_iterations,
            values=sorted_values,
            weight=float(weight),
            negative=negative,
            bounds=bounds,
            max_degree=criterion_degree,
        )

    return model.total_rating()
