import json
import os
import sys

if __name__ == "__main__":
    # при прямом запуске (python3 libs/libmathmodel.py) sys.path[0] — это libs/,
    # а не корень репозитория, поэтому psb_conf не находится без этой правки.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from allta import MathModel

from psb_conf import SCRIPT_DIR


RPS_BOUNDS = (0.0, 500.0)
LATENCY_BOUNDS_MS = (0.0, 20_000.0)
FAIL_RATE_BOUNDS_PCT = (0.0, 100.0)

RPS_WEIGHT = 0.5
LATENCY_WEIGHT = 0.3
FAIL_RATE_WEIGHT = 0.2

DEFAULT_POWER = 0.786622


def get_total_rating_info_sys(results: dict, power: float = DEFAULT_POWER):
    """Считает итоговый рейтинг по результатам нагрузочного теста info-sys.

    Критерии по оси числа отправленных запросов (requests) внутри уровня:
    - rps (positive) — чем выше, тем лучше;
    - latency (negative) — средняя задержка, чем ниже, тем лучше;
    - fail_rate (negative) — доля запросов без ok_200 (403/401/other/err_total), %.

    """
    keys = sorted(results, key=lambda k: int(k))

    requests = [float(results[k]["requests"]) for k in keys]
    rps = [float(results[k]["rps"]) for k in keys]
    latency = [float(results[k]["avg_latency_ms"]) for k in keys]
    fail_rate = [
        100.0 * (results[k]["requests"] - results[k]["ok_200"]) / results[k]["requests"]
        for k in keys
    ]

    model = MathModel()
    model.add_criterion(
        "rps", iterations=requests, values=rps,
        weight=RPS_WEIGHT, negative=False, bounds=RPS_BOUNDS,
    )
    model.add_criterion(
        "latency", iterations=requests, values=latency,
        weight=LATENCY_WEIGHT, negative=True, bounds=LATENCY_BOUNDS_MS,
    )
    model.add_criterion(
        "fail_rate", iterations=requests, values=fail_rate,
        weight=FAIL_RATE_WEIGHT, negative=True, bounds=FAIL_RATE_BOUNDS_PCT,
    )
    result = model.total_rating(power=power)

    if isinstance(result, dict):
        total_rating = result["total_rating"]
    elif hasattr(result, "total"):
        total_rating = result.total
    else:
        total_rating = result

    return {"total_rating": int(round(float(total_rating)))}


if __name__ == "__main__":
    with open(f"{SCRIPT_DIR}/mrd_load_level1_results.json", "r") as file:
        data = json.load(file)

    result = get_total_rating_info_sys(data)
    print(f"total_rating: {result['total_rating']}")
