import contextlib
import importlib.util
import io
from pathlib import Path

from allta import OldMathModel, MathModel


def _load_real_datasets_module():
    module_path = Path(__file__).parent / "3.py"
    spec = importlib.util.spec_from_file_location("real_datasets_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


REAL_DATASETS = _load_real_datasets_module()

DATASET_EXT4_PARSEC_1_7_5_UU1_5_10 = REAL_DATASETS.DATASET_EXT4_PARSEC_1_7_5_UU1_5_10
DATASET_EXT4_PARSEC_1_7_6_UU1_5_15 = REAL_DATASETS.DATASET_EXT4_PARSEC_1_7_6_UU1_5_15
NEGATIVE_METRICS = REAL_DATASETS.NEGATIVE_METRICS
OLD_BOUNDS_BY_METRIC = REAL_DATASETS.OLD_BOUNDS_BY_METRIC
ALT_BOUNDS_BY_METRIC = REAL_DATASETS.ALT_BOUNDS_BY_METRIC


def build_scaled_dataset_from_dataset(base_dataset, improvement_factor):
    dataset = {}
    for iteration_str, metrics in base_dataset.items():
        dataset[iteration_str] = {}
        for metric_name, metric_data in metrics.items():
            base_value = float(metric_data["value"])
            weight = float(metric_data["weight"])
            if metric_name in NEGATIVE_METRICS:
                value = base_value / float(improvement_factor)
            else:
                value = base_value * float(improvement_factor)
            dataset[iteration_str][metric_name] = {
                "weight": weight,
                "value": float(value),
            }
    return dataset


def build_math_model_from_dataset(dataset):
    return build_math_model_from_dataset_with_bounds(dataset, ALT_BOUNDS_BY_METRIC)


def build_math_model_from_dataset_with_bounds(dataset, bounds_by_metric):
    grouped = {}
    for iteration_str, metrics in dataset.items():
        iteration = float(iteration_str)
        for metric_name, metric_data in metrics.items():
            grouped.setdefault(metric_name, {"iterations": [], "values": [], "weight": None})
            grouped[metric_name]["iterations"].append(iteration)
            grouped[metric_name]["values"].append(float(metric_data["value"]))
            if grouped[metric_name]["weight"] is None:
                grouped[metric_name]["weight"] = float(metric_data["weight"])

    model = MathModel()
    for metric_name, payload in grouped.items():
        model.add_criterion(
            metric_name,
            payload["iterations"],
            payload["values"],
            payload["weight"],
            metric_name in NEGATIVE_METRICS,
            bounds_by_metric[metric_name],
        )
    return model


def derive_signed_ratio_range_from_factors(factors):
    factor_values = sorted(float(factor) for factor in factors if float(factor) > 0.0 and float(factor) != 1.0)
    if not factor_values:
        return (-4.0, 4.0)

    lower_factors = [factor for factor in factor_values if factor < 1.0]
    upper_factors = [factor for factor in factor_values if factor > 1.0]

    signed_left = -(1.0 / min(lower_factors)) if lower_factors else -1.0
    signed_right = max(upper_factors) if upper_factors else 1.0

    if abs(signed_left) <= 1.0 and signed_right <= 1.0:
        return (-4.0, 4.0)
    return (float(signed_left), float(signed_right))


def calculate_alt_power_once(dataset, factors):
    signed_ratio_range = derive_signed_ratio_range_from_factors(factors)
    alt_bounds_by_metric = build_alt_bounds_for_factor_range(dataset, signed_ratio_range)
    model = build_math_model_from_dataset_with_bounds(dataset, alt_bounds_by_metric)
    with contextlib.redirect_stdout(io.StringIO()):
        debug = model.calc_power(
            sample_count=80,
            random_seed=42,
            signed_ratio_range=signed_ratio_range,
            bounds_margin=1e-6,
        )
    return float(debug["power"]), alt_bounds_by_metric


def build_alt_bounds_for_factor_range(
    base_dataset,
    signed_ratio_range,
    fill_ratio=0.88,
):
    ratio_min, ratio_max = resolve_signed_ratio_range(signed_ratio_range)
    if not 0.0 < float(fill_ratio) < 1.0:
        raise ValueError("fill_ratio должен быть в диапазоне (0, 1)")

    bounds = {}
    metric_names = next(iter(base_dataset.values())).keys()
    for metric_name in metric_names:
        base_values = [
            float(iter_metrics[metric_name]["value"])
            for iter_metrics in base_dataset.values()
        ]
        observed_max = max(base_values)

        if metric_name in NEGATIVE_METRICS:
            worst_case_max = observed_max / float(ratio_min)
        else:
            worst_case_max = observed_max * float(ratio_max)

        if worst_case_max <= 0.0:
            bounds[metric_name] = ALT_BOUNDS_BY_METRIC[metric_name]
            continue

        upper_bound = worst_case_max / float(fill_ratio)
        default_lower, default_upper = ALT_BOUNDS_BY_METRIC[metric_name]
        upper_bound = max(float(upper_bound), float(default_upper) * 0.25)

        if metric_name == "sync":
            upper_bound = max(upper_bound, 10.0)

        bounds[metric_name] = (float(default_lower), float(upper_bound))

    return bounds


def resolve_signed_ratio_range(signed_ratio_range):
    left_value = float(signed_ratio_range[0])
    right_value = float(signed_ratio_range[1])
    if left_value == 0.0 or right_value == 0.0:
        raise ValueError("signed_ratio_range не должен содержать 0")

    resolved = []
    for value in (left_value, right_value):
        if value > 0.0:
            resolved.append(float(value))
        else:
            resolved.append(1.0 / abs(float(value)))
    return min(resolved), max(resolved)


def calc_old_model(dataset):
    grouped = {}
    for iteration_str, metrics in dataset.items():
        iteration = float(iteration_str)
        for metric_name, metric_data in metrics.items():
            grouped.setdefault(metric_name, {"iterations": [], "values": [], "weight": None})
            grouped[metric_name]["iterations"].append(iteration)
            grouped[metric_name]["values"].append(float(metric_data["value"]))
            if grouped[metric_name]["weight"] is None:
                grouped[metric_name]["weight"] = float(metric_data["weight"])

    model = OldMathModel(max_degree=3, normalize_integral=False)
    for metric_name, payload in grouped.items():
        model.add_criterion(
            metric_name,
            payload["iterations"],
            payload["values"],
            payload["weight"],
            metric_name in NEGATIVE_METRICS,
            OLD_BOUNDS_BY_METRIC[metric_name],
            max_degree=3,
        )
    return float(model.total_rating()["total_rating"])


def calc_alt_model(dataset, area_power, bounds_by_metric):
    model = build_math_model_from_dataset_with_bounds(dataset, bounds_by_metric)
    result = model.total_rating(power=float(area_power))
    return float(result["total_rating"])


def print_unified_table(title, dataset_names, ratings_old, ratings_alt):
    print(f"\n=== {title} ===")
    print(
        f"{'Dataset':<12} {'cmp to':>14} "
        f"{'OLD rate':>12} {'OLD ratio':>10} {'OLD Δ%':>9} "
        f"{'ALT rate':>12} {'ALT ratio':>10} {'ALT Δ%':>9}"
    )
    print("-" * 104)

    reference_name = next((name for name in dataset_names if "(1)" in name or "(1.0)" in name), dataset_names[0])
    old_ref = ratings_old[reference_name]
    alt_ref = ratings_alt[reference_name]

    for idx, left in enumerate(dataset_names):
        if idx < len(dataset_names) - 1:
            right = dataset_names[idx + 1]
            cmp_to = "-> " + right
            old_base = ratings_old[left]
            old_target = ratings_old[right]
            alt_base = ratings_alt[left]
            alt_target = ratings_alt[right]
        else:
            cmp_to = "/ " + reference_name
            old_base = ratings_old[reference_name]
            old_target = ratings_old[left]
            alt_base = ratings_alt[reference_name]
            alt_target = ratings_alt[left]

        old_left = ratings_old[left]
        alt_left = ratings_alt[left]

        old_ratio = (old_left / old_ref) if old_ref != 0 else 0.0
        alt_ratio = (alt_left / alt_ref) if alt_ref != 0 else 0.0
        old_delta = ((old_target - old_base) / old_base * 100.0) if old_base != 0 else 0.0
        alt_delta = ((alt_target - alt_base) / alt_base * 100.0) if alt_base != 0 else 0.0

        print(
            f"{left:<12} {cmp_to:>14} "
            f"{old_left:>12.6f} {old_ratio:>10.3f} {old_delta:>8.2f}% "
            f"{alt_left:>12.6f} {alt_ratio:>10.3f} {alt_delta:>8.2f}%"
        )


if __name__ == "__main__":
    display_factors = [
        0.25,
        0.38,
        0.43,
        0.50,
        0.57,
        0.64,
        0.72,
        0.79,
        0.86,
        0.92,
        1.0,
        1.25,
        1.3,
        1.5,
        1.8,
        2.0,
        3.0,
        4.0,
    ]
    calibration_factors = list(display_factors)

    profiles = [
        ("ext4-parsec 1.7.5.UU1 5-10", DATASET_EXT4_PARSEC_1_7_5_UU1_5_10),
        ("ext4-parsec 1.7.6.UU1 5-15", DATASET_EXT4_PARSEC_1_7_6_UU1_5_15),
    ]

    for profile_title, base_dataset in profiles:
        alt_area_power, alt_bounds_by_metric = calculate_alt_power_once(
            base_dataset,
            [factor for factor in calibration_factors if factor != 1.0],
        )

        datasets = {}
        for idx, factor in enumerate(display_factors, start=1):
            datasets[f"ds_{idx}({factor:g})"] = build_scaled_dataset_from_dataset(
                base_dataset,
                factor,
            )
        dataset_names = list(datasets.keys())

        ratings_old = {name: calc_old_model(ds) for name, ds in datasets.items()}
        ratings_alt = {
            name: calc_alt_model(ds, alt_area_power, alt_bounds_by_metric)
            for name, ds in datasets.items()
        }

        metrics_count = len(next(iter(base_dataset.values())))
        neg_count = sum(1 for metric_name in next(iter(base_dataset.values())).keys() if metric_name in NEGATIVE_METRICS)
        pos_count = metrics_count - neg_count
        total_w = sum(float(metric_data["weight"]) for metric_data in next(iter(base_dataset.values())).values())
        title = (
            f"{profile_title}; metrics={metrics_count} "
            f"(positive={pos_count}, negative={neg_count}, weights_sum={total_w:.3f}, "
            f"alt_power={alt_area_power:.3f}, calibration=[0.25;4.0])"
        )
        print_unified_table(title, dataset_names, ratings_old, ratings_alt)
