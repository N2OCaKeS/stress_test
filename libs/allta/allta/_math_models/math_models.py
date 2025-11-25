from dataclasses import dataclass
from typing import Dict, Iterable, Sequence, Union


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
        values: Iterable[float],
        *,
        lower: float | None = None,
        upper: float | None = None,
    ):
        """
        Нормализует значения в диапазон [0, 1] с учётом необязательных границ.

        Args:
            values (Iterable[float]): Последовательность чисел.
            lower (float, optional): Нижняя граница (значения ниже будут подняты).
            upper (float, optional): Верхняя граница (значения выше будут обрезаны).

        Returns:
            list[float]: Нормализованные значения.
        """
        vals = [float(v) for v in values]
        if not vals:
            return []

        if lower is not None:
            vals = [max(v, lower) for v in vals]
        if upper is not None:
            vals = [min(v, upper) for v in vals]

        vmin, vmax = min(vals), max(vals)
        if vmax == vmin:
            # Если одно значение (или все равны) — считаем его максимально нормализованным.
            return [1.0 for _ in vals]
        return [(v - vmin) / (vmax - vmin) for v in vals]

    @staticmethod
    def approximate(values: Sequence[float], kind: str = "linear"):
        """
        Строит функцию аппроксимации для ряда.

        Args:
            values (Sequence[float]): Ряд значений.
            kind (str, optional): ``"linear"`` — линейный тренд (МНК),
                ``"interp"`` — линейная интерполяция.

        Returns:
            Callable[[float], float]: Функция f(x), оценивающая значение ряда.
        """

        ys = [float(v) for v in values]
        if not ys:
            return lambda x: 0.0
        if len(ys) == 1:
            const = ys[0]
            return lambda x: const

        xs = list(range(len(ys)))

        if kind == "interp":

            def interp(x: float) -> float:
                if x <= 0:
                    return ys[0]
                if x >= len(ys) - 1:
                    return ys[-1]
                left = int(x)
                right = left + 1
                frac = x - left
                return ys[left] + frac * (ys[right] - ys[left])

            return interp

        # Линейный тренд через МНК: y = a*x + b
        n = len(xs)
        sum_x = sum(xs)
        sum_y = sum(ys)
        sum_xy = sum(x * y for x, y in zip(xs, ys))
        sum_x2 = sum(x * x for x in xs)
        denom = n * sum_x2 - sum_x * sum_x
        if denom == 0:
            a = 0.0
        else:
            a = (n * sum_xy - sum_x * sum_y) / denom
        b = (sum_y - a * sum_x) / n

        return lambda x: a * x + b

    @staticmethod
    def total_rating(
        criteria: Iterable[Criterion],
        *,
        aggregate: str = "mean",
        normalize: bool = False,
    ):
        """
        Считает Total Rating и вклады по критериям.

        Формула: R = Σ (weight_i * y_i * sign_i), где y_i — агрегированное
        значение критерия, sign_i = 1 для позитивных, -1 для негативных.

        Args:
            criteria (Iterable[Criterion]): Набор критериев.
            aggregate (str, optional): Агрегатор значений (``"mean"``, ``"last"``, ``"max"``, ``"min"``).
            normalize (bool, optional): Нормализовать ли значения перед агрегацией.

        Returns:
            tuple: (rating: float, contributions: Dict[str, float])

        Raises:
            ValueError: Если сумма весов критериев не равна 1.
        """

        crit_list = list(criteria)

        weights_sum = sum(c.weight for c in crit_list)
        if abs(weights_sum - 1.0) > 1e-6:
            raise ValueError(
                f"Сумма весов критериев должна быть 1, сейчас {weights_sum}"
            )

        total = 0.0
        contributions: Dict[str, float] = {}
        for c in crit_list:
            vals = MathModels._as_sequence(c.values)
            if normalize:
                proc_vals = MathModels.normalize(
                    vals, lower=c.lower_bound, upper=c.upper_bound
                )
            else:
                proc_vals = [float(v) for v in vals]

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
    ):
        """
        Возвращает Total Rating и вклады в виде словаря.

        Args:
            criteria (Iterable[Criterion]): Набор критериев.
            aggregate (str, optional): Агрегатор значений (``"mean"``, ``"last"``, ``"max"``, ``"min"``).
            normalize (bool, optional): Нормализовать ли значения.

        Returns:
            dict: Ключи ``"rating"`` и ``"contributions"``.
        """

        rating, contributions = MathModels.total_rating(
            criteria, aggregate=aggregate, normalize=normalize
        )
        return {"rating": rating, "contributions": contributions}
