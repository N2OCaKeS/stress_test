from dataclasses import dataclass
from typing import Dict, Iterable, Sequence, Union


@dataclass
class NewCriterion:
    """
    Описание критерия для расчёта Total Rating.

    Args:
        name (str): Название метрики.
        values (float | Sequence[float]): Одно значение или последовательность замеров.
        weight (float): Вес критерия (сумма весов всех критериев = 1).
        sign (int, optional): 1 для позитивного критерия, -1 для негативного.
        scale (float, optional): Масштабный коэффициент s_i для метрики.
                                 По умолчанию 1.0 (без доп. масштаба).
        lower_bound / upper_bound: зарезервировано для старой модели, здесь не используются.
    """

    name: str
    values: Union[float, Sequence[float]]
    weight: float
    sign: int = 1
    scale: float = 1.0
    lower_bound: float | None = None
    upper_bound: float | None = None


class NewMathModel:
    """
    Новая мат. модель без базовых значений и нормализации.

    Формулы:

        Для каждой метрики i и её значений x_{i,j}:

            y_{i,j} = s_i * x_{i,j},     если sign_i =  1  (больше — лучше)
            y_{i,j} = s_i / x_{i,j},     если sign_i = -1  (меньше — лучше)

        S_i   = (1 / n_i) * Σ_j y_{i,j}      — среднее по метрике
        R_raw = Σ_i weight_i * S_i           — «сырой» рейтинг (без глобального масштаба)

        R     = rating_scale * R_raw         — финальный рейтинг для отображения

    Где s_i задаётся в Criterion.scale и фиксирован для всех запусков.
    """

    @staticmethod
    def _as_sequence(values: Union[float, Sequence[float]]) -> Sequence[float]:
        """
        Оборачивает одиночное значение в список или приводит последовательность к списку.
        """
        if isinstance(values, (int, float)):
            return [float(values)]
        if isinstance(values, str):
            return [float(values)]
        return [float(v) for v in values]

    @staticmethod
    def total_rating(
        criteria: Iterable[NewCriterion],
        *,
        epsilon: float = 1e-9,
    ) -> tuple[float, Dict[str, float]]:
        """
        Считает «сырой» Total Rating по новой модели (без глобального масштаба).

        Returns:
            (rating_raw, contributions_raw), где
            - rating_raw: итоговый рейтинг R_raw;
            - contributions_raw: вклад по каждой метрике (без глобального масштаба).
        """
        crit_list = list(criteria)
        weights_sum = sum(c.weight for c in crit_list)
        if abs(weights_sum - 1.0) > 1e-6:
            raise ValueError(
                f"Сумма весов критериев должна быть 1, сейчас {weights_sum}"
            )

        total_raw = 0.0
        contributions_raw: Dict[str, float] = {}

        for c in crit_list:
            vals = NewMathModel._as_sequence(c.values)
            if not vals:
                contributions_raw[c.name] = 0.0
                continue

            s_i = float(c.scale) if c.scale is not None else 1.0

            acc = 0.0
            for v in vals:
                x = float(v)
                if c.sign >= 0:
                    # Позитивная метрика: больше — лучше
                    y = s_i * x
                else:
                    # Негативная метрика: меньше — лучше
                    denom = x if x != 0.0 else epsilon
                    y = s_i / denom
                acc += y

            s_mean = acc / len(vals)           # S_i — среднее по метрике
            contrib_raw = c.weight * s_mean    # вклад метрики в общий рейтинг (raw)
            contributions_raw[c.name] = contrib_raw
            total_raw += contrib_raw

        return total_raw, contributions_raw

    @staticmethod
    def to_dict(
        criteria: Iterable[NewCriterion],
        *,
        epsilon: float = 1e-9,
        rating_scale: float = 1000.0,
    ) -> Dict[str, Dict[str, float] | float]:
        """
        Возвращает Total Rating и вклады в виде словаря.

        Args:
            criteria: Набор критериев.
            epsilon: защита от деления на ноль.
            rating_scale: глобальный множитель для масштаба рейтинга (по умолчанию 1000).

        Returns:
            {
                "rating": float,             # уже умножен на rating_scale
                "rating_raw": float,         # без глобального масштаба
                "contributions": {           # вклады с учётом rating_scale
                    name: float,
                    ...
                },
                "contributions_raw": {       # вклады без глобального масштаба
                    name: float,
                    ...
                },
            }
        """
        rating_raw, contributions_raw = NewMathModel.total_rating(
            criteria,
            epsilon=epsilon,
        )

        rating = rating_raw * rating_scale
        contributions = {
            name: value * rating_scale for name, value in contributions_raw.items()
        }

        return {
            "rating": rating,
            "rating_raw": rating_raw,
            "contributions": contributions,
            "contributions_raw": contributions_raw,
        }
