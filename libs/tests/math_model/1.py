import contextlib
import io

from allta import OldMathModel, MathModel

CLIENTS = [100, 200, 300, 400, 500]

# Профиль 1: текущие метрики (как было).
METRICS_SET_1 = {
    "la": {
        "weight": 0.2,
        "direction": "negative",
        "base_values": [80.0, 120.0, 180.0, 240.0, 320.0],
        "bounds": (0.0, 700.0),
    },
    "tps1": {
        "weight": 0.4,
        "direction": "positive",
        "base_values": [3000.0, 2800.0, 2500.0, 2200.0, 2000.0],
        "bounds": (0.0, 140000.0),
    },
    "tps2": {
        "weight": 0.4,
        "direction": "positive",
        "base_values": [3050.0, 2850.0, 2550.0, 2250.0, 2050.0],
        "bounds": (0.0, 140000.0),
    },
}

# Профиль 2: дополнительные 10 метрик с разными весами и позитивностью.
METRICS_SET_2 = {
    "p95_latency_ms": {
        "weight": 0.12,
        "direction": "negative",
        "base_values": [180.0, 220.0, 260.0, 320.0, 380.0],
        "bounds": (0.0, 1500.0),
    },
    "error_rate_ppm": {
        "weight": 0.08,
        "direction": "negative",
        "base_values": [1200.0, 1500.0, 1900.0, 2500.0, 3200.0],
        "bounds": (0.0, 50000.0),
    },
    "cpu_usage_units": {
        "weight": 0.09,
        "direction": "negative",
        "base_values": [550.0, 620.0, 700.0, 780.0, 860.0],
        "bounds": (0.0, 2000.0),
    },
    "memory_pressure": {
        "weight": 0.09,
        "direction": "negative",
        "base_values": [400.0, 480.0, 560.0, 650.0, 740.0],
        "bounds": (0.0, 2000.0),
    },
    "queue_depth": {
        "weight": 0.08,
        "direction": "negative",
        "base_values": [60.0, 90.0, 130.0, 180.0, 240.0],
        "bounds": (0.0, 2000.0),
    },
    "api_rps": {
        "weight": 0.12,
        "direction": "positive",
        "base_values": [14000.0, 13200.0, 12100.0, 11000.0, 9800.0],
        "bounds": (0.0, 200000.0),
    },
    "db_tps": {
        "weight": 0.11,
        "direction": "positive",
        "base_values": [21000.0, 20000.0, 18500.0, 17000.0, 15500.0],
        "bounds": (0.0, 250000.0),
    },
    "cache_hits_ps": {
        "weight": 0.10,
        "direction": "positive",
        "base_values": [30000.0, 28600.0, 26800.0, 25000.0, 23000.0],
        "bounds": (0.0, 400000.0),
    },
    "completed_jobs": {
        "weight": 0.11,
        "direction": "positive",
        "base_values": [9000.0, 8600.0, 8100.0, 7600.0, 7000.0],
        "bounds": (0.0, 150000.0),
    },
    "network_throughput_mb": {
        "weight": 0.10,
        "direction": "positive",
        "base_values": [4200.0, 4000.0, 3700.0, 3400.0, 3100.0],
        "bounds": (0.0, 80000.0),
    },
}

def validate_profile(metrics_profile):
    for metric_name, cfg in metrics_profile.items():
        direction = cfg["direction"]
        if direction not in {"positive", "negative"}:
            raise ValueError(
                f"{metric_name}: direction должен быть positive или negative"
            )
        if len(cfg["base_values"]) != len(CLIENTS):
            raise ValueError(
                f"{metric_name}: base_values длины {len(cfg['base_values'])}, "
                f"ожидается {len(CLIENTS)}"
            )
        lower, upper = cfg["bounds"]
        if float(upper) <= float(lower):
            raise ValueError(f"{metric_name}: upper bound должен быть > lower bound")


def extract_model_meta(metrics_profile):
    negative_metrics = {
        metric_name
        for metric_name, cfg in metrics_profile.items()
        if cfg["direction"] == "negative"
    }
    bounds_by_metric = {
        metric_name: cfg["bounds"] for metric_name, cfg in metrics_profile.items()
    }
    return negative_metrics, bounds_by_metric


def build_dataset_from_profile(metrics_profile, improvement_factor):
    dataset = {}
    for idx, clients in enumerate(CLIENTS):
        dataset[str(clients)] = {}
        for metric_name, cfg in metrics_profile.items():
            base_value = float(cfg["base_values"][idx])
            direction = cfg["direction"]

            if direction == "negative":
                value = base_value / float(improvement_factor)
            else:
                value = base_value * float(improvement_factor)

            dataset[str(clients)][metric_name] = {
                "weight": float(cfg["weight"]),
                "value": float(value),
            }
    return dataset


def build_math_model_from_dataset(dataset, negative_metrics, bounds_by_metric):
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
            metric_name in negative_metrics,
            bounds_by_metric[metric_name],
        )
    return model


def calculate_alt_power_once(dataset, negative_metrics, bounds_by_metric, relative_factors):
    signed_ratio_range = derive_signed_ratio_range_from_factors(relative_factors)
    alt_bounds_by_metric = build_alt_bounds_for_factor_range(
        dataset,
        negative_metrics,
        bounds_by_metric,
        signed_ratio_range,
    )
    model = build_math_model_from_dataset(dataset, negative_metrics, alt_bounds_by_metric)
    with contextlib.redirect_stdout(io.StringIO()):
        debug = model.calc_power(
            sample_count=80,
            random_seed=42,
            signed_ratio_range=signed_ratio_range,
            bounds_margin=1e-6,
        )
    return float(debug["power"]), alt_bounds_by_metric


def derive_signed_ratio_range_from_factors(factors):
    factor_values = sorted(
        float(factor)
        for factor in factors
        if float(factor) > 0.0 and float(factor) != 1.0
    )
    if not factor_values:
        return (-4.0, 4.0)

    lower_factors = [factor for factor in factor_values if factor < 1.0]
    upper_factors = [factor for factor in factor_values if factor > 1.0]

    signed_left = -(1.0 / min(lower_factors)) if lower_factors else -1.0
    signed_right = max(upper_factors) if upper_factors else 1.0

    if abs(signed_left) <= 1.0 and signed_right <= 1.0:
        return (-4.0, 4.0)
    return (float(signed_left), float(signed_right))


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


def build_alt_bounds_for_factor_range(
    base_dataset,
    negative_metrics,
    default_bounds_by_metric,
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

        if metric_name in negative_metrics:
            worst_case_max = observed_max / float(ratio_min)
        else:
            worst_case_max = observed_max * float(ratio_max)

        if worst_case_max <= 0.0:
            bounds[metric_name] = default_bounds_by_metric[metric_name]
            continue

        upper_bound = worst_case_max / float(fill_ratio)
        default_lower, default_upper = default_bounds_by_metric[metric_name]
        upper_bound = max(float(upper_bound), float(default_upper) * 0.25)

        bounds[metric_name] = (float(default_lower), float(upper_bound))

    return bounds


def calc_old_model(dataset, negative_metrics, bounds_by_metric):
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
            metric_name in negative_metrics,
            bounds_by_metric[metric_name],
            max_degree=3,
        )
    return float(model.total_rating()["total_rating"])


def calc_alt_model(dataset, negative_metrics, bounds_by_metric, area_power):
    model = build_math_model_from_dataset(dataset, negative_metrics, bounds_by_metric)
    return float(model.total_rating(power=float(area_power))["total_rating"])


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
    factors = [
        0.1, # 0,001
        0.15,
        0.20,
        0.25, # 25
        0.38,
        0.43,
        0.50,
        0.57,
        0.64,
        0.72,
        0.79,
        0.86,
        0.92,
        1.0, # 100
        1.25,
        1.3,
        1.5,
        1.8,
        2.0,
        3.0,
        4.0, # 400
        6.0,
        8.0, # 2000
    ]

    profiles = [
        ("dataset_1 (current 3 metrics)", METRICS_SET_1),
        ("dataset_2 (extra 10 metrics)", METRICS_SET_2),
    ]

    for profile_title, metrics_profile in profiles:
        validate_profile(metrics_profile)
        negative_metrics, bounds_by_metric = extract_model_meta(metrics_profile)
        base_dataset = build_dataset_from_profile(metrics_profile, 1.0)
        alt_area_power, alt_bounds_by_metric = calculate_alt_power_once(
            base_dataset,
            negative_metrics,
            bounds_by_metric,
            [factor for factor in factors if factor != 1.0],
        )

        datasets = {}
        for idx, factor in enumerate(factors, start=1):
            datasets[f"ds_{idx}({factor:g})"] = build_dataset_from_profile(
                metrics_profile,
                factor,
            )
        dataset_names = list(datasets.keys())

        ratings_old = {
            name: calc_old_model(ds, negative_metrics, bounds_by_metric)
            for name, ds in datasets.items()
        }
        ratings_alt = {
            name: calc_alt_model(ds, negative_metrics, alt_bounds_by_metric, alt_area_power)
            for name, ds in datasets.items()
        }

        neg_count = len(negative_metrics)
        pos_count = len(metrics_profile) - neg_count
        total_w = sum(float(cfg["weight"]) for cfg in metrics_profile.values())
        title = (
            f"{profile_title}; metrics={len(metrics_profile)} "
            f"(positive={pos_count}, negative={neg_count}, weights_sum={total_w:.3f}, "
            f"alt_power={alt_area_power:.3f})"
        )
        print_unified_table(title, dataset_names, ratings_old, ratings_alt)
