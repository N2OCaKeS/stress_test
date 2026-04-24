import contextlib
import io

from allta import OldMathModel, MathModel
# Реальные наборы данных (из тестов).
DATASET_1 = {
    "100": {
        "la": {"weight": 0.33, "value": 8.482},
        "tps1": {"weight": 0.83, "value": 11789.016818},
        "tps2": {"weight": 0.83, "value": 11791.092539},
    },
    "200": {
        "la": {"weight": 0.33, "value": 17.97},
        "tps1": {"weight": 0.83, "value": 11129.553002},
        "tps2": {"weight": 0.83, "value": 11130.303031},
    },
    "300": {
        "la": {"weight": 0.33, "value": 31.627},
        "tps1": {"weight": 0.83, "value": 9485.622431},
        "tps2": {"weight": 0.83, "value": 9486.123688},
    },
    "400": {
        "la": {"weight": 0.33, "value": 46.598},
        "tps1": {"weight": 0.83, "value": 8584.144843},
        "tps2": {"weight": 0.83, "value": 8584.491484},
    },
    "500": {
        "la": {"weight": 0.33, "value": 61.064},
        "tps1": {"weight": 0.83, "value": 8188.122856},
        "tps2": {"weight": 0.83, "value": 8188.385106},
    },
}

DATASET_2 = {
    "100": {
        "la": {"weight": 0.33, "value": 5.386},
        "tps1": {"weight": 0.83, "value": 18566.441029},
        "tps2": {"weight": 0.83, "value": 18568.963702},
    },
    "200": {
        "la": {"weight": 0.33, "value": 8.672},
        "tps1": {"weight": 0.83, "value": 23062.364656},
        "tps2": {"weight": 0.83, "value": 23067.077549},
    },
    "300": {
        "la": {"weight": 0.33, "value": 15.003},
        "tps1": {"weight": 0.83, "value": 19995.914244},
        "tps2": {"weight": 0.83, "value": 19998.627232},
    },
    "400": {
        "la": {"weight": 0.33, "value": 18.983},
        "tps1": {"weight": 0.83, "value": 21071.687911},
        "tps2": {"weight": 0.83, "value": 21074.072323},
    },
    "500": {
        "la": {"weight": 0.33, "value": 25.212},
        "tps1": {"weight": 0.83, "value": 19831.720727},
        "tps2": {"weight": 0.83, "value": 19833.653092},
    },
}

# ext4-parsec 1.7.5.UU1 5-10
# Для syscall-критериев используем *_avg как единое значение критерия.
DATASET_EXT4_PARSEC_1_7_5_UU1_5_10 = {
    "10000": {
        "app_overhead": {"weight": 0.125, "value": 177749.0},
        "create": {"weight": 0.5625, "value": 35.0},
        "write": {"weight": 0.5625, "value": 6.0},
        "fsync": {"weight": 0.5625, "value": 192.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 1.0},
        "unlink": {"weight": 0.5625, "value": 15.0},
        "speed": {"weight": 1.0, "value": 3926.0},
    },
    "20000": {
        "app_overhead": {"weight": 0.125, "value": 376467.0},
        "create": {"weight": 0.5625, "value": 38.0},
        "write": {"weight": 0.5625, "value": 7.0},
        "fsync": {"weight": 0.5625, "value": 208.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 1.0},
        "unlink": {"weight": 0.5625, "value": 15.0},
        "speed": {"weight": 1.0, "value": 3644.0},
    },
    "30000": {
        "app_overhead": {"weight": 0.125, "value": 524937.0},
        "create": {"weight": 0.5625, "value": 34.0},
        "write": {"weight": 0.5625, "value": 6.0},
        "fsync": {"weight": 0.5625, "value": 186.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 1.0},
        "unlink": {"weight": 0.5625, "value": 15.0},
        "speed": {"weight": 1.0, "value": 4058.7},
    },
    "40000": {
        "app_overhead": {"weight": 0.125, "value": 684404.0},
        "create": {"weight": 0.5625, "value": 35.0},
        "write": {"weight": 0.5625, "value": 6.0},
        "fsync": {"weight": 0.5625, "value": 188.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 1.0},
        "unlink": {"weight": 0.5625, "value": 15.0},
        "speed": {"weight": 1.0, "value": 3998.5},
    },
    "50000": {
        "app_overhead": {"weight": 0.125, "value": 946175.0},
        "create": {"weight": 0.5625, "value": 37.0},
        "write": {"weight": 0.5625, "value": 7.0},
        "fsync": {"weight": 0.5625, "value": 201.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 1.0},
        "unlink": {"weight": 0.5625, "value": 15.0},
        "speed": {"weight": 1.0, "value": 3740.2},
    },
    "60000": {
        "app_overhead": {"weight": 0.125, "value": 1024823.0},
        "create": {"weight": 0.5625, "value": 35.0},
        "write": {"weight": 0.5625, "value": 6.0},
        "fsync": {"weight": 0.5625, "value": 184.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 1.0},
        "unlink": {"weight": 0.5625, "value": 14.0},
        "speed": {"weight": 1.0, "value": 4074.7},
    },
    "70000": {
        "app_overhead": {"weight": 0.125, "value": 1192987.0},
        "create": {"weight": 0.5625, "value": 35.0},
        "write": {"weight": 0.5625, "value": 6.0},
        "fsync": {"weight": 0.5625, "value": 186.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 1.0},
        "unlink": {"weight": 0.5625, "value": 15.0},
        "speed": {"weight": 1.0, "value": 4051.5},
    },
    "80000": {
        "app_overhead": {"weight": 0.125, "value": 1551453.0},
        "create": {"weight": 0.5625, "value": 40.0},
        "write": {"weight": 0.5625, "value": 7.0},
        "fsync": {"weight": 0.5625, "value": 203.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 1.0},
        "unlink": {"weight": 0.5625, "value": 15.0},
        "speed": {"weight": 1.0, "value": 3667.5},
    },
    "90000": {
        "app_overhead": {"weight": 0.125, "value": 1507136.0},
        "create": {"weight": 0.5625, "value": 36.0},
        "write": {"weight": 0.5625, "value": 6.0},
        "fsync": {"weight": 0.5625, "value": 183.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 1.0},
        "unlink": {"weight": 0.5625, "value": 15.0},
        "speed": {"weight": 1.0, "value": 4089.6},
    },
}

# ext4-parsec 1.7.6.UU1 5-15
# Для syscall-критериев используем *_avg как единое значение критерия.
DATASET_EXT4_PARSEC_1_7_6_UU1_5_15 = {
    "10000": {
        "app_overhead": {"weight": 0.125, "value": 215521.0},
        "create": {"weight": 0.5625, "value": 44.0},
        "write": {"weight": 0.5625, "value": 8.0},
        "fsync": {"weight": 0.5625, "value": 209.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 2.0},
        "unlink": {"weight": 0.5625, "value": 20.0},
        "speed": {"weight": 1.0, "value": 3481.2},
    },
    "20000": {
        "app_overhead": {"weight": 0.125, "value": 577111.0},
        "create": {"weight": 0.5625, "value": 57.0},
        "write": {"weight": 0.5625, "value": 10.0},
        "fsync": {"weight": 0.5625, "value": 242.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 3.0},
        "unlink": {"weight": 0.5625, "value": 24.0},
        "speed": {"weight": 1.0, "value": 2916.4},
    },
    "30000": {
        "app_overhead": {"weight": 0.125, "value": 688111.0},
        "create": {"weight": 0.5625, "value": 46.0},
        "write": {"weight": 0.5625, "value": 8.0},
        "fsync": {"weight": 0.5625, "value": 207.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 2.0},
        "unlink": {"weight": 0.5625, "value": 22.0},
        "speed": {"weight": 1.0, "value": 3470.2},
    },
    "40000": {
        "app_overhead": {"weight": 0.125, "value": 1027214.0},
        "create": {"weight": 0.5625, "value": 53.0},
        "write": {"weight": 0.5625, "value": 10.0},
        "fsync": {"weight": 0.5625, "value": 233.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 3.0},
        "unlink": {"weight": 0.5625, "value": 21.0},
        "speed": {"weight": 1.0, "value": 3069.5},
    },
    "50000": {
        "app_overhead": {"weight": 0.125, "value": 1136337.0},
        "create": {"weight": 0.5625, "value": 47.0},
        "write": {"weight": 0.5625, "value": 8.0},
        "fsync": {"weight": 0.5625, "value": 206.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 2.0},
        "unlink": {"weight": 0.5625, "value": 21.0},
        "speed": {"weight": 1.0, "value": 3473.4},
    },
    "60000": {
        "app_overhead": {"weight": 0.125, "value": 1689835.0},
        "create": {"weight": 0.5625, "value": 57.0},
        "write": {"weight": 0.5625, "value": 10.0},
        "fsync": {"weight": 0.5625, "value": 243.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 3.0},
        "unlink": {"weight": 0.5625, "value": 21.0},
        "speed": {"weight": 1.0, "value": 2917.4},
    },
    "70000": {
        "app_overhead": {"weight": 0.125, "value": 1608999.0},
        "create": {"weight": 0.5625, "value": 49.0},
        "write": {"weight": 0.5625, "value": 8.0},
        "fsync": {"weight": 0.5625, "value": 205.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 2.0},
        "unlink": {"weight": 0.5625, "value": 21.0},
        "speed": {"weight": 1.0, "value": 3457.7},
    },
    "80000": {
        "app_overhead": {"weight": 0.125, "value": 1869944.0},
        "create": {"weight": 0.5625, "value": 49.0},
        "write": {"weight": 0.5625, "value": 9.0},
        "fsync": {"weight": 0.5625, "value": 204.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 2.0},
        "unlink": {"weight": 0.5625, "value": 21.0},
        "speed": {"weight": 1.0, "value": 3461.8},
    },
    "90000": {
        "app_overhead": {"weight": 0.125, "value": 2063826.0},
        "create": {"weight": 0.5625, "value": 47.0},
        "write": {"weight": 0.5625, "value": 8.0},
        "fsync": {"weight": 0.5625, "value": 203.0},
        "sync": {"weight": 0.5625, "value": 0.0},
        "close": {"weight": 0.5625, "value": 2.0},
        "unlink": {"weight": 0.5625, "value": 21.0},
        "speed": {"weight": 1.0, "value": 3501.7},
    },
}

NEGATIVE_METRICS = {
    "la",
    "app_overhead",
    "create",
    "write",
    "fsync",
    "sync",
    "close",
    "unlink",
}
OLD_BOUNDS_BY_METRIC = {
    "la": (0.0, 700.0),
    "tps1": (0.0, 140000.0),
    "tps2": (0.0, 140000.0),
    "app_overhead": (0.0, 72600000.0),
    "create": (0.0, 1300.0),
    "write": (0.0, 270.0),
    "fsync": (0.0, 98000.0),
    "sync": (0.0, 10.0),
    "close": (0.0, 100.0),
    "unlink": (0.0, 100.0),
    "speed": (0.0, 96000.0),
}

ALT_BOUNDS_BY_METRIC = {
    "la": (0.0, 700.0),
    "tps1": (0.0, 140000.0),
    "tps2": (0.0, 140000.0),
    "app_overhead": (0.0, 10000000.0),
    "create": (0.0, 250.0),
    "write": (0.0, 50.0),
    "fsync": (0.0, 1200.0),
    "sync": (0.0, 10.0),
    "close": (0.0, 20.0),
    "unlink": (0.0, 100.0),
    "speed": (0.0, 20000.0),
}

CURRENT_FIXED_POWER = 0.48


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


def calc_alt_model(dataset):
    model = build_math_model_from_dataset(dataset)
    return float(model.total_rating(power=float(CURRENT_FIXED_POWER))["total_rating"])


def calc_alt_calculated_model(dataset):
    model = build_math_model_from_dataset(dataset)
    with contextlib.redirect_stdout(io.StringIO()):
        debug = model.calc_power()
    calculated_power = float(debug["power"])
    result = model.total_rating(power=calculated_power)
    return calculated_power, float(result["total_rating"])


def calc_alt_calt_model(dataset):
    model = build_math_model_from_dataset(dataset)
    with contextlib.redirect_stdout(io.StringIO()):
        debug = model.calc_power()
    calculated_power = float(debug["power"])
    result = model.total_rating(power=calculated_power)
    return calculated_power, float(result["total_rating"])


def build_math_model_from_dataset(dataset):
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
            ALT_BOUNDS_BY_METRIC[metric_name],
        )
    return model


def print_unified_table(
    dataset_names,
    ratings_old,
    ratings_alt_fixed,
    ratings_alt_calculated,
    ratings_alt_calt,
):
    print("\n=== Единая сводная таблица OLD / ALT fixed / ALT calculated / ALT calt ===")
    print(
        f"{'Dataset':<30} {'cmp to':>32} "
        f"{'OLD rate':>11} {'OLD ratio':>10} {'OLD Δ%':>9} "
        f"{'ALT fixed':>11} {'FIX ratio':>10} {'FIX Δ%':>9} "
        f"{'ALT calc':>11} {'CALC ratio':>10} {'CALC Δ%':>9} "
        f"{'ALT calt':>11} {'CALT ratio':>10} {'CALT Δ%':>9}"
    )
    print("-" * 235)

    for idx, left in enumerate(dataset_names):
        if idx < len(dataset_names) - 1:
            right = dataset_names[idx + 1]
            cmp_to = "-> " + right
            old_base = ratings_old[left]
            old_target = ratings_old[right]
            alt_fixed_base = ratings_alt_fixed[left]
            alt_fixed_target = ratings_alt_fixed[right]
            alt_calc_base = ratings_alt_calculated[left]
            alt_calc_target = ratings_alt_calculated[right]
            alt_calt_base = ratings_alt_calt[left]
            alt_calt_target = ratings_alt_calt[right]
        else:
            first_name = dataset_names[0]
            cmp_to = "/ " + first_name
            old_base = ratings_old[first_name]
            old_target = ratings_old[left]
            alt_fixed_base = ratings_alt_fixed[first_name]
            alt_fixed_target = ratings_alt_fixed[left]
            alt_calc_base = ratings_alt_calculated[first_name]
            alt_calc_target = ratings_alt_calculated[left]
            alt_calt_base = ratings_alt_calt[first_name]
            alt_calt_target = ratings_alt_calt[left]

        old_left = ratings_old[left]
        alt_fixed_left = ratings_alt_fixed[left]
        alt_calc_left = ratings_alt_calculated[left]
        alt_calt_left = ratings_alt_calt[left]

        old_ratio = (old_target / old_base) if old_base != 0 else 0.0
        alt_fixed_ratio = (alt_fixed_target / alt_fixed_base) if alt_fixed_base != 0 else 0.0
        alt_calc_ratio = (alt_calc_target / alt_calc_base) if alt_calc_base != 0 else 0.0
        alt_calt_ratio = (alt_calt_target / alt_calt_base) if alt_calt_base != 0 else 0.0
        old_delta = ((old_target - old_base) / old_base * 100.0) if old_base != 0 else 0.0
        alt_fixed_delta = (
            ((alt_fixed_target - alt_fixed_base) / alt_fixed_base * 100.0)
            if alt_fixed_base != 0
            else 0.0
        )
        alt_calc_delta = (
            ((alt_calc_target - alt_calc_base) / alt_calc_base * 100.0)
            if alt_calc_base != 0
            else 0.0
        )
        alt_calt_delta = (
            ((alt_calt_target - alt_calt_base) / alt_calt_base * 100.0)
            if alt_calt_base != 0
            else 0.0
        )
        print(
            f"{left:<30} "
            f"{cmp_to:>32} "
            f"{old_left:>11.6f} {old_ratio:>10.3f} {old_delta:>8.2f}% "
            f"{alt_fixed_left:>11.6f} {alt_fixed_ratio:>10.3f} {alt_fixed_delta:>8.2f}% "
            f"{alt_calc_left:>11.6f} {alt_calc_ratio:>10.3f} {alt_calc_delta:>8.2f}% "
            f"{alt_calt_left:>11.6f} {alt_calt_ratio:>10.3f} {alt_calt_delta:>8.2f}%"
        )


if __name__ == "__main__":
    datasets = {
        "ext4-parsec 1.7.5.UU1 5-10": DATASET_EXT4_PARSEC_1_7_5_UU1_5_10,
        "ext4-parsec 1.7.6.UU1 5-15": DATASET_EXT4_PARSEC_1_7_6_UU1_5_15,
    }
    dataset_names = list(datasets.keys())

    ratings_old = {name: calc_old_model(ds) for name, ds in datasets.items()}
    ratings_alt_fixed = {name: calc_alt_model(ds) for name, ds in datasets.items()}
    calculated_results = {name: calc_alt_calculated_model(ds) for name, ds in datasets.items()}
    calculated_powers = {name: result[0] for name, result in calculated_results.items()}
    ratings_alt_calculated = {name: result[1] for name, result in calculated_results.items()}
    calt_results = {name: calc_alt_calt_model(ds) for name, ds in datasets.items()}
    calt_powers = {name: result[0] for name, result in calt_results.items()}
    ratings_alt_calt = {name: result[1] for name, result in calt_results.items()}

    print("\n=== Power values by ALT model ===")
    print(
        f"{'Dataset':<30} "
        f"{'ALT fixed':>12} "
        f"{'ALT calc':>12} "
        f"{'ALT calt':>12}"
    )
    print("-" * 70)
    for name in dataset_names:
        print(
            f"{name:<30} "
            f"{CURRENT_FIXED_POWER:>12.6f} "
            f"{calculated_powers[name]:>12.6f} "
            f"{calt_powers[name]:>12.6f}"
        )

    print_unified_table(
        dataset_names,
        ratings_old,
        ratings_alt_fixed,
        ratings_alt_calculated,
        ratings_alt_calt,
    )
