from dataclasses import dataclass
from typing import Dict, Iterable, Sequence, Union
from sklearn.preprocessing import MinMaxScaler
import numpy as np
from numpy.exceptions import RankWarning
import warnings

from scipy import integrate

@dataclass
class Criterion:
    """
    Описание критерия для расчёта Total Rating.

    Args:
        name (str): Название метрики.
        values (float | Sequence[float]): Одно значение или последовательность замеров.
        weight (float): Вес критерия (сумма весов всех критериев = 1).
        sign (int, optional): 1 для позитивного критерия, -1 для негативного.
        lower_bound (float, optional): Нижняя граница для нормализации (если normalize=True).
        upper_bound (float, optional): Верхняя граница для нормализации (если normalize=True).
    """

    name: str
    values: Union[float, Sequence[float]]
    weight: float
    sign: int = 1
    lower_bound: float | None = None
    upper_bound: float | None = None


class MathModels:
    """
    Нормализация, аппроксимация, расчёт Total Rating и генерация словарей.

    Основные функции:
    - ``normalize`` — нормализация значений в диапазон [0, 1].
    - ``approximate`` — построение функции аппроксимации по ряду.
    - ``total_rating`` — расчёт суммарного рейтинга и вкладов.
    - ``to_dict`` — удобный возврат рейтинга и вкладов в виде словаря.
    """

    @staticmethod
    def normalize(
        values: Iterable[float | int],
        *,
        lower: float | int | None = None,
        upper: float | int | None = None,
    ):
        """
        Нормализует значения в диапазон [0, 1] или [-1, 1] с учётом необязательных границ.

        Args:
            values (Iterable[float | int]): Последовательность чисел.
            lower (float | int, optional): Нижняя граница (значения ниже будут подняты).
            upper (float | int, optional): Верхняя граница (значения выше будут обрезаны).

        Returns:
            list[float]: Нормализованные значения.
        """
        data = np.array([float(v) for v in values], dtype=float)
        if data.size == 0:
            return []

        if lower is not None and upper is not None and float(lower) > float(upper):
            raise ValueError("lower bound must not exceed upper bound")

        # Ограничиваем значения по заданным границам, чтобы сохранять исходный порядок.
        clipped = data.copy()
        if lower is not None:
            clipped = np.maximum(clipped, float(lower))
        if upper is not None:
            clipped = np.minimum(clipped, float(upper))

        fit_values = clipped
        if lower is not None:
            fit_values = np.insert(fit_values, 0, float(lower))
        if upper is not None:
            fit_values = np.append(fit_values, float(upper))

        scaler = MinMaxScaler()
        scaled = scaler.fit_transform(fit_values.reshape(-1, 1)).ravel()

        start = 1 if lower is not None else 0
        end = start + len(clipped)
        return scaled[start:end].tolist()

    @staticmethod
    def approximate(
        x: Sequence[float],
        y: Sequence[float],
        degree: int = 10,
    ):
        """
        Строит функцию аппроксимации для ряда.

        Args:
            x (Sequence[float]): Значения оси X (например, время замеров).
            y (Sequence[float]): Измеренные значения метрики.
            degree (int, optional): Максимальная степень полинома для подбора.

        Returns:
            Callable[[float], float]: Функция f(x), оценивающая значение ряда.
        """
        x_arr = np.array(x, dtype=float)
        y_arr = np.array(y, dtype=float)

        if x_arr.size != y_arr.size:
            raise ValueError("x and y must have the same length")
        if x_arr.size < 2:
            raise ValueError("at least two points are required for approximation")

        max_degree = min(int(degree), x_arr.size - 1)
        for deg in range(max_degree, 0, -1):
            with warnings.catch_warnings():
                warnings.filterwarnings("error", category=RankWarning)
                try:
                    return np.poly1d(np.polyfit(x_arr, y_arr, deg))
                except RankWarning:
                    continue

        # Запасной вариант: постоянная функция.
        return np.poly1d(np.polyfit(x_arr, y_arr, 0))

    @staticmethod
    def total_rating(
        criteria: Iterable[Criterion],
        *,
        aggregate: str = "mean",
        normalize: bool = False,
        aproximate: bool = False,
        normalize_range: str = "0-1",
    ):
        """
        Считает Total Rating и вклады по критериям.

        Формула: R = Σ (weight_i * y_i * sign_i), где y_i — агрегированное
        значение критерия, sign_i = 1 для позитивных, -1 для негативных.
        Итоговый рейтинг не опускается ниже 0; при этом вклады остаются исходными.

        Args:
            criteria (Iterable[Criterion]): Набор критериев.
            aggregate (str, optional): Агрегатор значений (``"mean"``, ``"last"``, ``"max"``, ``"min"``).
            normalize (bool, optional): Нормализовать ли значения перед агрегацией.
            normalize_range (str, optional): Целевой диапазон нормализации, ``"0-1"`` или ``"-1-1"``.

        Returns:
            tuple: (rating: float, contributions: Dict[str, float])

        Raises:
            ValueError: Если сумма весов критериев не равна 1.
        """

        crit_list = list(criteria)

        # weights_sum = sum(c.weight for c in crit_list) TODO >0,99 =< 1
        # if abs(weights_sum - 1.0) > 1e-6:
        #     raise ValueError(
        #         f"Сумма весов критериев должна быть 1, сейчас {weights_sum}"
        #     )

        total = 0.0
        contributions: Dict[str, float] = {}
        for c in crit_list:
            vals = MathModels._as_sequence(c.values)
            if normalize:
                proc_vals = MathModels.normalize(
                    vals,
                    lower=c.lower_bound,
                    upper=c.upper_bound,
                    # target_range=normalize_range,
                )
            else:
                proc_vals = [float(v) for v in vals]

            if aproximate:
                x_points = list(range(len(proc_vals)))
                if len(x_points) > 1:
                    func = MathModels.approximate(x_points, proc_vals)
                    area, _ = integrate.quad(func, x_points[0], x_points[-1])
                    span = float(x_points[-1] - x_points[0])
                    proc_vals = [area / span] if span != 0 else [proc_vals[-1]]

            if not proc_vals:
                y_i = 0.0
            elif aggregate == "last":
                y_i = proc_vals[-1]
            elif aggregate == "max":
                y_i = max(proc_vals)
            elif aggregate == "min":
                y_i = min(proc_vals)
            else:
                y_i = sum(proc_vals) / len(proc_vals)
            base = c.weight * y_i
            contrib = base if c.sign >= 0 else -base
            contributions[c.name] = contrib
            total += contrib
        # Не позволяем итоговому рейтингу уходить в отрицательные значения.
        total = max(total, 0.0)
        return total, contributions

    @staticmethod
    def _as_sequence(values: Union[float, Sequence[float]]) -> Iterable[float]:
        """
        Оборачивает одиночное значение в список или приводит последовательность к списку.

        Args:
            values (float | Sequence[float]): Одно значение или последовательность.

        Returns:
            Iterable[float]: Последовательность чисел.

        Raises:
            TypeError: Если передан неподдерживаемый тип.
        """
        if isinstance(values, (int, float)):
            return [float(values)]
        # Защита от строк, которые технически итерируемы, но не подходят
        if isinstance(values, str):
            try:
                return [float(values)]
            except ValueError as e:
                raise TypeError(
                    "Criterion.values must be float or sequence of floats"
                ) from e
        try:
            return list(values)
        except TypeError as e:
            raise TypeError(
                "Criterion.values must be float or sequence of floats"
            ) from e

    @staticmethod
    def to_dict(
        criteria: Iterable[Criterion],
        *,
        aggregate: str = "mean",
        normalize: bool = False,
        normalize_range: str = "0-1",
    ):
        """
        Возвращает Total Rating и вклады в виде словаря.

        Args:
            criteria (Iterable[Criterion]): Набор критериев.
            aggregate (str, optional): Агрегатор значений (``"mean"``, ``"last"``, ``"max"``, ``"min"``).
            normalize (bool, optional): Нормализовать ли значения.
            normalize_range (str, optional): Целевой диапазон нормализации, ``"0-1"`` или ``"-1-1"``.

        Returns:
            dict: Ключи ``"rating"`` и ``"contributions"``.
        """

        rating, contributions = MathModels.total_rating(
            criteria,
            aggregate=aggregate,
            normalize=normalize,
            normalize_range=normalize_range,
        )
        return {"rating": rating, "contributions": contributions}
