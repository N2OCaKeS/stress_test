import json
import sys

from allta import MathModel
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))
from lib import system
from config.conf import (
    RESULTS_MAIN_DIR, 
    KERNEL_CRITERIONS,
    PROCESSES_IPC_CRITERIONS,
    FILESYSTEM_CRITERIONS,
    SCRIPTS_CRITERIONS,
    TOTAL_TEMPLATE_COLOR
)




with open(f"{RESULTS_MAIN_DIR}/testing_sbsdates.json", "r") as f:
    dates = json.load(f)

SUBSYSTEM_DATES = {
    'kernel': {'crit': KERNEL_CRITERIONS,
               'power': 0.998,
               'handle': ['/', '1']},
    'processes_ipc': {'crit': PROCESSES_IPC_CRITERIONS,
                      'power': 0.947,
                      'handle': ['*', '100']},
    'filesystem': {'crit': FILESYSTEM_CRITERIONS,
                   'power': 0.974,
                   'handle': ['/', '100000']},
    'scripts': {'crit': SCRIPTS_CRITERIONS,
                'power': 0.916,
                'handle': ['*', '100']}
}

results_dict = {
    subsys: {
        'total_rating': 0
    } for subsys in SUBSYSTEM_DATES.keys()
}

fpower_dict = {
    subsys: {
        'power': 0
    } for subsys in SUBSYSTEM_DATES.keys()
}

class IndexCalculator:

    def subsystem_index_calculator(self,
                                   criterions: dict, 
                                   subsystem: str, 
                                   power: float,
                                   handle: list = None,
                                   power_calc: bool = False) -> str | bool:
        
        if isinstance(power, float):
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

        if power_calc:
            fpower = _model.calc_power()
            fpower_dict[subsystem] = fpower['power']
            print(fpower_dict)

            return fpower_dict

        result = _model.total_rating(power=fp) 

        total_rating = round((result['total_rating']), 3)
        print(f"{subsystem} power: {fp}")
        print(f"{subsystem} total_rating: {total_rating}")

        if handle:
            if handle[0] == '/':
                handle_total_rating = (float(total_rating) / int(handle[1]))
            elif handle[0] == '*':
                handle_total_rating = (float(total_rating) * int(handle[1]))

            return round(float(handle_total_rating), 1)
        
        return total_rating


    def start_subsystem_calc(self, power_calc=None):
        for subsystem, values in SUBSYSTEM_DATES.items():
            results_dict[subsystem] = self.subsystem_index_calculator(criterions=values['crit'], 
                                                                      subsystem=subsystem, 
                                                                      power=values['power'],
                                                                      handle=values['handle'],
                                                                      power_calc=power_calc)


        print(results_dict)
        
        return results_dict




    def total_index_calculator(self, 
                               power_calc: bool = False):
        
        if power_calc:
            return print(f"\n\n{self.start_subsystem_calc(power_calc=True)}")

        subsystem_dates = self.start_subsystem_calc()
        system_info = system.get_system_info()

        summ_tr = sum(list(subsystem_dates.values()))

        print(summ_tr)
        print(type(summ_tr))
        print(TOTAL_TEMPLATE_COLOR.format(kernel=subsystem_dates['kernel'],
                                        processes_ipc=subsystem_dates['processes_ipc'],
                                        filesystem=subsystem_dates['filesystem'],
                                        scripts=subsystem_dates['scripts'],
                                        total=summ_tr,
                                        **system_info))




ic = IndexCalculator()
ic.total_index_calculator(power_calc=False)







