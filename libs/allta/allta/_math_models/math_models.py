from dataclasses import dataclass, replace
from typing import Dict, Iterable, Sequence, Union, cast, overload
from sklearn.preprocessing import MinMaxScaler
import numpy as np
from numpy.exceptions import RankWarning
import warnings

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
    - ``normalize_criteria`` — глобальная нормализация по всем критериям с последующим разбиением.
    - ``approximate`` — построение функции аппроксимации по ряду.
    - ``total_rating`` — расчёт суммарного рейтинга и вкладов.
    - ``to_dict`` — удобный возврат рейтинга и вкладов в виде словаря.
    """

    @overload
    @staticmethod
    def normalize(
        values: Criterion,
        *,
        lower: float | int | None = None,
        upper: float | int | None = None,
        target_range: str = "0-1",
    ) -> list[float]: ...

    @overload
    @staticmethod
    def normalize(
        values: Iterable[Criterion],
        *,
        lower: float | int | None = None,
        upper: float | int | None = None,
        target_range: str = "0-1",
    ) -> list[Criterion]: ...

    @overload
    @staticmethod
    def normalize(
        values: Union[float, int, Iterable[float | int]],
        *,
        lower: float | int | None = None,
        upper: float | int | None = None,
        target_range: str = "0-1",
    ) -> list[float]: ...

    @staticmethod
    def normalize(
        values: Union[float, int, Iterable[float | int], Criterion, Iterable[Criterion]],
        *,
        lower: float | int | None = None,
        upper: float | int | None = None,
        target_range: str = "0-1",
    ) -> list[float] | list[Criterion]:
        """
        Нормализует значения в диапазон [0, 1] или [-1, 1] через ``sklearn.preprocessing.MinMaxScaler``.

        Args:
            values: Последовательность чисел, ``Criterion`` или iterable из ``Criterion`` (в этом случае
                каждый критерий нормализуется отдельно с его ``lower_bound``/``upper_bound``).
            lower: Нижняя граница исходных значений; всё, что ниже, поднимется до неё. Для списка
                критериев используются их собственные границы.
            upper: Верхняя граница исходных значений; всё, что выше, обрежется до неё. Для списка
                критериев используются их собственные границы.
            target_range: Диапазон нормализации: ``"0-1"`` или ``"-1-1"``.

        Returns:
            list[float] | list[Criterion]: Нормализованные значения или обновлённые критерии.
        """
        criteria_seq = MathModels._as_criteria_list(values)
        if criteria_seq is not None:
            normalized: list[Criterion] = []
            for criterion in criteria_seq:
                norm_vals = MathModels._normalize_values(
                    criterion.values,
                    lower=criterion.lower_bound,
                    upper=criterion.upper_bound,
                    target_range=target_range,
                )
                normalized.append(replace(criterion, values=norm_vals))
            return normalized

        if isinstance(values, Criterion):
            effective_lower = values.lower_bound if values.lower_bound is not None else lower
            effective_upper = values.upper_bound if values.upper_bound is not None else upper
            return MathModels._normalize_values(
                values.values,
                lower=effective_lower,
                upper=effective_upper,
                target_range=target_range,
            )

        narrowed_values = cast(Union[float, int, Iterable[float | int]], values)
        return MathModels._normalize_values(
            narrowed_values,
            lower=lower,
            upper=upper,
            target_range=target_range,
        )


    @staticmethod
    def normalize_criteria(
        criteria: Iterable[Criterion],
        *,
        lower: float | int | None = None,
        upper: float | int | None = None,
        target_range: str = "0-1",
    ) -> Dict[str, list[float]]:
        """
        Глобально нормализует значения всех критериев одним проходом, сохраняя их структуру.

        Алгоритм: все значения объединяются в один список, нормализуются через ``normalize``,
        после чего результат разбивается обратно по исходным критериям в прежнем порядке.

        Args:
            criteria: Критерии с исходными значениями.
            lower: Общая нижняя граница (опционально).
            upper: Общая верхняя граница (опционально).
            target_range: Диапазон нормализации (``"0-1"`` или ``"-1-1"``).

        Returns:
            dict[str, list[float]]: Нормализованные значения по каждому критерию.
        """
        crit_list = list(criteria)

        segments: list[tuple[str, int]] = []
        flat_values: list[float] = []
        for c in crit_list:
            vals = list(MathModels._as_sequence(c.values))
            segments.append((c.name, len(vals)))
            flat_values.extend(vals)

        normalized_flat = cast(
            list[float],
            MathModels.normalize(
                flat_values,
                lower=lower,
                upper=upper,
                target_range=target_range,
            ),
        )

        if len(normalized_flat) != len(flat_values):
            raise ValueError("Ошибка нормализации: размер результата не совпал с исходным")

        result: Dict[str, list[float]] = {}
        index = 0
        for name, length in segments:
            slice_vals = normalized_flat[index : index + length] if length else []
            result[name] = slice_vals
            index += length

        return result

    @staticmethod
    def approximate(
        x: Union[Sequence[float], Criterion],
        y: Union[Sequence[float], Criterion],
        degree: int = 10,
    ) -> np.poly1d:
        """
        Строит полиномиальную аппроксимацию ряда, постепенно понижая степень при проблемах с ранком.

        Args:
            x: Значения оси X (например, моменты замеров) или ``Criterion`` с этими значениями.
            y: Измеренные значения метрики или ``Criterion`` с ними.
            degree: Максимальная степень полинома для подбора (степень понижается, если fit неустойчив).

        Returns:
            Callable[[float], float]: Функция ``f(x)`` для оценки значения ряда.
        """

        x_seq = MathModels._as_sequence(
            x.values if isinstance(x, Criterion) else x
        )
        y_seq = MathModels._as_sequence(
            y.values if isinstance(y, Criterion) else y
        )

        x_arr = np.array(x_seq, dtype=float)
        y_arr = np.array(y_seq, dtype=float)

        if x_arr.size != y_arr.size:
            raise ValueError("x и y должны быть одинаковой длины")
        if x_arr.size < 2:
            raise ValueError("для аппроксимации нужно минимум две точки")

        polinom_factor = min(int(degree), x_arr.size - 1)
        while polinom_factor > 0:
            with warnings.catch_warnings():
                warnings.filterwarnings("error", category=RankWarning)
                try:
                    coeffs = np.polyfit(x_arr, y_arr, polinom_factor)
                    return np.poly1d(coeffs)
                except RankWarning:
                    polinom_factor -= 1

        return np.poly1d(np.polyfit(x_arr, y_arr, 0))

    @staticmethod
    def total_rating(
        criteria: Iterable[Criterion],
        *,
        aggregate: str = "mean",
        normalize: bool = True,
        normalize_range: str = "0-1",
    ) -> tuple[float, Dict[str, float]]:
        """
        Считает Total Rating и вклады по критериям.

        Формула: ``R = Σ_i (a_i · f(y_i))^{-1}, a_i > 0``.
        Здесь ``a_i`` — вес критерия, ``y_i`` — агрегированное нормализованное значение метрики
        (нормализация выполняется внутри функции). Для знаков метрик используем разные ``f``:
        - позитивные (``sign >= 0``): ``f(y_i) = (y_i + ε)^{-1}``, вклад ``c_i = (y_i + ε) / a_i``;
        - негативные (``sign < 0``): ``f(y_i) = (y_i + ε)``, вклад ``c_i = 1 / (a_i · (y_i + ε))``.
        Все вклады остаются неотрицательными.

        Args:
            criteria: Набор критериев.
            aggregate: Агрегатор значений (``"mean"``, ``"last"``, ``"max"``, ``"min"``).
            normalize: Оставлено для обратной совместимости; значения всегда нормализуются.
            normalize_range: Диапазон нормализации (``"0-1"`` или ``"-1-1"``).

        Returns:
            tuple[float, Dict[str, float]]: Итоговый рейтинг и словарь вкладов.
        """

        crit_list = list(criteria)

        weights_sum = sum(c.weight for c in crit_list)
        if weights_sum <= 0.9 or weights_sum > 1.0:
            raise ValueError(
                f"Сумма весов критериев должна быть в диапазоне (0.99; 1], сейчас {weights_sum}"
            )
        if any(c.weight <= 0 for c in crit_list):
            raise ValueError("Все веса критериев должны быть положительными для формулы Total Rating")

        if not normalize:
            warnings.warn(
                "total_rating теперь всегда нормализует значения; задайте normalize=True (по умолчанию), "
                "чтобы отключить предупреждение.",
                DeprecationWarning,
                stacklevel=2,
            )

        processed_criteria = cast(
            list[Criterion],
            MathModels.normalize(crit_list, target_range=normalize_range),
        )

        total = 0.0
        contributions: Dict[str, float] = {}
        # Минимальное значение нормализованной метрики, чтобы отрицательные критерии
        # не давали взрывной вклад при y_i = 0. 
        # TODO если y_i(не нормализованный) = 0, сделать 1
        epsilon = 0.001
        for c in processed_criteria:
            proc_vals = [float(v) for v in MathModels._as_sequence(c.values)]

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

            safe_y = y_i if y_i > 0 else 0.0
            base = safe_y + epsilon

            if c.sign >= 0:
                contrib = base / c.weight
            else:
                contrib = 1.0 / (c.weight * base)
            contributions[c.name] = contrib
            total += contrib

        return total, contributions

    @staticmethod
    def to_dict(
        criteria: Iterable[Criterion],
        *,
        aggregate: str = "mean",
        normalize: bool = False,
        normalize_range: str = "0-1",
    ) -> Dict[str, Dict[str, float] | float]:
        """
        Возвращает Total Rating и вклады в виде словаря.

        Args:
            criteria: Набор критериев.
            aggregate: Агрегатор значений (``"mean"``, ``"last"``, ``"max"``, ``"min"``).
            normalize: Нормализовать ли значения перед агрегацией.
            normalize_range: Диапазон нормализации (``"0-1"`` или ``"-1-1"``).

        Returns:
            dict: ``{"rating": float, "contributions": {name: value, ...}}``.
        """
        rating, contributions = MathModels.total_rating(
            criteria,
            aggregate=aggregate,
            normalize=normalize,
            normalize_range=normalize_range,
        )
        return {"rating": rating, "contributions": contributions}

    @staticmethod
    def _as_sequence(values: Union[float, Sequence[float], Iterable[float | int]]) -> Iterable[float]:
        """
        Оборачивает одиночное значение в список или приводит последовательность к списку.

        Args:
            values (float | Sequence[float] | Iterable[float | int]): Одно значение или последовательность.

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
                    "Criterion.values должен быть числом или последовательностью чисел"
                ) from e
        try:
            return list(values)
        except TypeError as e:
            raise TypeError(
                "Criterion.values должен быть числом или последовательностью чисел"
            ) from e

    @staticmethod
    def _normalize_values(
        values: Union[float, int, Iterable[float | int]],
        *,
        lower: float | int | None = None,
        upper: float | int | None = None,
        target_range: str = "0-1",
    ) -> list[float]:
        """Внутренний нормализатор для работы с числовыми последовательностями."""
        seq = MathModels._as_sequence(values)
        data = np.array([float(v) for v in seq], dtype=float)
        if data.size == 0:
            return []

        if lower is not None and upper is not None and float(lower) > float(upper):
            raise ValueError("Нижняя граница не должна превышать верхнюю")

        if target_range == "0-1":
            feature_range: tuple[int, int] = (0, 1)
        elif target_range == "-1-1":
            feature_range = (-1, 1)
        else:
            raise ValueError('target_range должен быть "0-1" или "-1-1"')

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

        scaler = MinMaxScaler(feature_range=cast(tuple[int, int], feature_range))
        scaled = scaler.fit_transform(fit_values.reshape(-1, 1)).ravel()

        start = 1 if lower is not None else 0
        end = start + len(clipped)
        return scaled[start:end].tolist()

    @staticmethod
    def _looks_normalized(values: Sequence[float], *, target_range: str) -> bool:
        """Проверяет, укладываются ли значения в ожидаемый нормализованный диапазон."""
        if not values:
            return False

        min_val = min(values)
        max_val = max(values)
        eps = 1e-9

        if target_range == "0-1":
            return -eps <= min_val <= 1.0 + eps and -eps <= max_val <= 1.0 + eps
        if target_range == "-1-1":
            return -1.0 - eps <= min_val <= 1.0 + eps and -1.0 - eps <= max_val <= 1.0 + eps
        return False

    @staticmethod
    def _as_criteria_list(value: object) -> list[Criterion] | None:
        """Если объект — итерируемый набор Criterion, возвращает его списком, иначе None."""
        if isinstance(value, (str, bytes)):
            return None
        try:
            iterable = list(value)  # type: ignore[arg-type]
        except TypeError:
            return None
        return iterable if all(isinstance(item, Criterion) for item in iterable) else None
