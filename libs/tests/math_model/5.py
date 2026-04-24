from allta import MathModel

power = 0.902
CLIENTS = [100, 200, 300, 400, 500]
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

model = MathModel()

model.add_criterion(
    name="la",
    iterations=CLIENTS,
    values=METRICS_SET_1["la"]["base_values"],
    weight=METRICS_SET_1["la"]["weight"],
    negative=True,
    bounds=METRICS_SET_1["la"]["bounds"],
)

model.add_criterion(
    name="tps1",
    iterations=CLIENTS,
    values=METRICS_SET_1["tps1"]["base_values"],
    weight=METRICS_SET_1["tps1"]["weight"],
    negative=False,
    bounds=METRICS_SET_1["tps1"]["bounds"],
)

model.add_criterion(
    name="tps2",
    iterations=CLIENTS,
    values=METRICS_SET_1["tps2"]["base_values"],
    weight=METRICS_SET_1["tps2"]["weight"],
    negative=False,
    bounds=METRICS_SET_1["tps2"]["bounds"],
)

model.test_power(power=power)
