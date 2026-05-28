import json
import sys

from allta import MathModel
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.conf import RESULTS_MAIN_DIR, KERNEL_CRITERIONS




with open(f"{RESULTS_MAIN_DIR}/testing_sbsdates.json", "r") as f:
    dates = json.load(f)



def kernel_model():
    fixed_power = 0.9996180247850317
    k_model = MathModel()

    for test, conf in KERNEL_CRITERIONS.items():
        iterations = list(dates['kernel'][test].keys())
        print(f"""{test},
                iterations={iterations},
                values={[dates['kernel'][test][i]['value'] for i in iterations]},
                weight={conf['weight']},
                negative={conf['negative']},
                bounds={conf['bounds']}
            """)
        k_model.add_criterion(str(test),
                              iterations=iterations,
                              values=[dates['kernel'][test][i]['value'] for i in iterations],
                              weight=conf['weight'],
                              negative=conf['negative'],
                              bounds=conf['bounds'])

    result = k_model.total_rating(power=fixed_power)  

    #print(result)
    #print(result['total_rating'])
    print(type(result['total_rating']))

    total_rating = round((result['total_rating'] / 100), 1)
    print(f"total_rating: {total_rating}")

    return total_rating



kernel_model()
