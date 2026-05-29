import json
import sys

from allta import MathModel
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.conf import (
    RESULTS_MAIN_DIR, 
    KERNEL_CRITERIONS,
    PROCESSES_IPC_CRITERIONS,
    FILESYSTEM_CRITERIONS,
    SCRIPTS_CRITERIONS
)




with open(f"{RESULTS_MAIN_DIR}/testing_sbsdates.json", "r") as f:
    dates = json.load(f)

SUBSYSTEM_DATES = {
    'kernel': KERNEL_CRITERIONS,
    'processes_ipc': PROCESSES_IPC_CRITERIONS,
    'filesystem': FILESYSTEM_CRITERIONS,
    'scripts': SCRIPTS_CRITERIONS
}

results_dict = {
    subsys: {
        'total_rating': 0
    } for subsys in SUBSYSTEM_DATES.keys()
}




def index_calculator(criterions: dict, 
                     subsystem: str, 
                     power: int) -> str | bool:
    
    if isinstance(power, int):
        fp = power
    else:
        print(f"fixed_power не задан: {power}")
        return False

    _model = MathModel()

    for test, config in criterions.items():
        iterations = list(dates[subsystem][test].keys())
        
        print(
        f"""
            {test},
            iterations={iterations},
            values={[dates[subsystem][test][i]['value'] for i in iterations]},
            weight={config['weight']},
            negative={config['negative']},
            bounds={config['bounds']}
        """)

        _model.add_criterion(str(test),
                            iterations=iterations,
                            values=[dates[subsystem][test][i]['value'] for i in iterations],
                            weight=config['weight'],
                            negative=config['negative'],
                            bounds=config['bounds'])

    result = _model.total_rating(power=fp) 
    #print(result)
    #print(result['total_rating'])

    total_rating = round((result['total_rating']), 1)
    print(f"{subsystem} total_rating: {total_rating}")

    return total_rating


for subsystem, criterion in SUBSYSTEM_DATES.items():
    results_dict[subsystem] = index_calculator(criterions=criterion, subsystem=subsystem, power=int(0.99))


print(results_dict)

# TODO
# Проверить bounds во всех тестах
# Добавить в словарь метрики деления/умножения рейтинга
# Определить и добавить в словарь fixed power
