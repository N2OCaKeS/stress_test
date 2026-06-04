from __future__ import annotations

from pprint import pprint

try:
    from .math_model import MathModel
    from .old_math_model import OldMathModel, CriterionParams as OldCriterionParams, calculate_old_total_rating
except ImportError:
    from libs.allta.allta._math_models.math_model import MathModel
    from old_math_model import OldMathModel, CriterionParams as OldCriterionParams, calculate_old_total_rating


def example_old_class_individual() -> None:
    """OLD: задаём критерии по одному через add_criterion."""
    model = OldMathModel(max_degree=3, normalize_integral=True)

    model.add_criterion(
        "latency",
        iterations=[100, 200, 300],
        values=[12.0, 14.5, 17.0],
        weight=0.4,
        negative=True,
        bounds=(10.0, 20.0),
    )
    model.add_criterion(
        "throughput",
        iterations=[100, 200, 300],
        values=[5000.0, 7200.0, 9100.0],
        weight=0.6,
        negative=False,
        bounds=(4000.0, 10000.0),
    )

    result = model.total_rating()
    print("\nOLD class / individual criteria")
    pprint(result)


def example_old_with_common_params() -> None:
    """OLD: общий конфиг критериев через function API."""
    dataset = {
        100: {"latency": 12.0, "throughput": 5000.0, "cpu": 65.0},
        200: {"latency": 14.0, "throughput": 7400.0, "cpu": 72.0},
        300: {"latency": 18.0, "throughput": 9200.0, "cpu": 81.0},
    }

    criteria_params = {
        "*": OldCriterionParams(max_degree=2),
        "latency": {"weight": 0.35, "negative": True, "bounds": (10.0, 22.0)},
        "throughput": {"weight": 0.45, "negative": False, "bounds": (4000.0, 10000.0)},
        "cpu": {"weight": 0.20, "negative": True, "bounds": (30.0, 95.0)},
    }

    result = calculate_old_total_rating(
        dataset,
        normalize_integral=True,
        criteria_params=criteria_params,
    )
    print("\nOLD function / common + per-criterion params")
    pprint(result)


def example_math_model() -> None:
    """MathModel: тестовый запуск с подбором power и сравнением с текущим фиксированным значением."""
    current_fixed_power = 0.1
    model = MathModel()

    model.add_criterion(
        "latency",
        iterations=[100, 200, 300],
        values=[12.0, 14.0, 18.0],
        weight=0.4,
        negative=True,
        bounds=(10.0, 22.0),
    )
    model.add_criterion(
        "throughput",
        iterations=[100, 200, 300],
        values=[5000.0, 7300.0, 9300.0],
        weight=0.6,
        negative=False,
        bounds=(4000.0, 10000.0),
    )

    print("\nMathModel / calculated power")
    debug_result = model.calc_power()
    calculated_power = float(debug_result["power"])
    pprint(debug_result)

    print("\nMathModel / power comparison")
    print(f"current_fixed_power = {current_fixed_power}")
    print(f"calculated_power    = {calculated_power}")
    print(f"delta               = {calculated_power - current_fixed_power:+.6f}")

    print("\nMathModel / total rating with current fixed power")
    pprint(model.total_rating(power=current_fixed_power))

    print("\nMathModel / total rating with calculated power")
    pprint(model.total_rating(power=calculated_power))


def example_math_model_ratio() -> None:
    """MathModel(type='ratio'): рейтинг как отношение к эталону (без bounds и power).

    Эталон (референсный прогон) фиксируется один раз и передаётся в reference.
    На эталоне рейтинг = scale (100). value/ref для positive, ref/value для negative:
    ratio > 1 — лучше эталона, < 1 — хуже. Итог — взвешенное геом-среднее × scale.
    """
    # Эталон: значения референсного прогона по критериям (заморожены один раз).
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

    print("\nMathModel / ratio (отношение к эталону, scale=100)")
    result = model.total_rating(scale=100.0)   # эталон -> 100, >100 лучше, <100 хуже
    pprint(result)
    for name, info in result["criteria"].items():
        print(f"  {name}: R = {info['ratio']:.3f}")   # latency ~2.0, throughput ~1.5


if __name__ == "__main__":
    example_old_class_individual()
    example_old_with_common_params()
    example_math_model()
    example_math_model_ratio()
