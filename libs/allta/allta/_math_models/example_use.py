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


if __name__ == "__main__":
    example_old_class_individual()
    example_old_with_common_params()
    example_math_model()
