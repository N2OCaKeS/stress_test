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


def _group_series_by_metric(dataset):
    grouped = {}
    for iteration_str, metrics in dataset.items():
        iteration = float(iteration_str)
        for metric_name, metric_data in metrics.items():
            grouped.setdefault(metric_name, {"iterations": [], "values": [], "weight": None})
            grouped[metric_name]["iterations"].append(iteration)
            grouped[metric_name]["values"].append(float(metric_data["value"]))
            if grouped[metric_name]["weight"] is None:
                grouped[metric_name]["weight"] = float(metric_data["weight"])
    return grouped


def _build_old_model(dataset):
    grouped = _group_series_by_metric(dataset)
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
    return model


def _build_new_model(dataset, bounds_by_metric):
    grouped = _group_series_by_metric(dataset)
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


def calc_old_rating(dataset):
    return float(_build_old_model(dataset).total_rating()["total_rating"])


def calc_new_rating(dataset, power):
    return float(
        _build_new_model(dataset, ALT_BOUNDS_BY_METRIC).total_rating(power=float(power))["total_rating"]
    )


def calc_power_from_dataset(dataset):
    model = _build_new_model(dataset, ALT_BOUNDS_BY_METRIC)
    with contextlib.redirect_stdout(io.StringIO()):
        debug = model.calc_power(
            sample_count=80,
            random_seed=42,
            signed_ratio_range=(-4.0, 4.0),
            bounds_margin=1e-6,
        )
    return float(debug["power"])


def print_pair_summary(
    title,
    base_name,
    target_name,
    old_base,
    old_target,
    new_base,
    new_target,
    fixed_power,
):
    old_ratio = (old_target / old_base) if old_base != 0 else 0.0
    new_ratio = (new_target / new_base) if new_base != 0 else 0.0
    old_delta = ((old_target - old_base) / old_base * 100.0) if old_base != 0 else 0.0
    new_delta = ((new_target - new_base) / new_base * 100.0) if new_base != 0 else 0.0

    print(f"\n=== {title} ===")
    print(f"calibrated power on {base_name}: {fixed_power:.6f}")
    print(
        f"{'Dataset':<34} {'OLD rating':>12} {'NEW rating':>12} "
        f"{'vs base OLD':>12} {'vs base NEW':>12}"
    )
    print("-" * 90)
    print(f"{base_name:<34} {old_base:>12.6f} {new_base:>12.6f} {1.0:>12.3f} {1.0:>12.3f}")
    print(
        f"{target_name:<34} {old_target:>12.6f} {new_target:>12.6f} "
        f"{old_ratio:>12.3f} {new_ratio:>12.3f}"
    )
    print(f"OLD delta: {old_delta:.2f}%")
    print(f"NEW delta: {new_delta:.2f}%")


if __name__ == "__main__":
    power_dataset_1 = calc_power_from_dataset(DATASET_1)
    old_1 = calc_old_rating(DATASET_1)
    old_2 = calc_old_rating(DATASET_2)
    new_1 = calc_new_rating(DATASET_1, power_dataset_1)
    new_2 = calc_new_rating(DATASET_2, power_dataset_1)
    print_pair_summary(
        "Pair 1: DATASET_1 -> DATASET_2",
        "DATASET_1",
        "DATASET_2",
        old_1,
        old_2,
        new_1,
        new_2,
        power_dataset_1,
    )

    power_ext4 = calc_power_from_dataset(DATASET_EXT4_PARSEC_1_7_5_UU1_5_10)
    old_ext4_1 = calc_old_rating(DATASET_EXT4_PARSEC_1_7_5_UU1_5_10)
    old_ext4_2 = calc_old_rating(DATASET_EXT4_PARSEC_1_7_6_UU1_5_15)
    new_ext4_1 = calc_new_rating(DATASET_EXT4_PARSEC_1_7_5_UU1_5_10, power_ext4)
    new_ext4_2 = calc_new_rating(DATASET_EXT4_PARSEC_1_7_6_UU1_5_15, power_ext4)
    print_pair_summary(
        "Pair 2: EXT4 1.7.5.UU1 5-10 -> EXT4 1.7.6.UU1 5-15",
        "DATASET_EXT4_PARSEC_1_7_5_UU1_5_10",
        "DATASET_EXT4_PARSEC_1_7_6_UU1_5_15",
        old_ext4_1,
        old_ext4_2,
        new_ext4_1,
        new_ext4_2,
        power_ext4,
    )
