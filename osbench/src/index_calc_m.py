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
               'handle': ['/', '1'],
               'weight': 0.40},
    'processes_ipc': {'crit': PROCESSES_IPC_CRITERIONS,
                      'power': 0.947,
                      'handle': ['*', '100'],
                      'weight': 0.25},
    'filesystem': {'crit': FILESYSTEM_CRITERIONS,
                   'power': 0.974,
                   'handle': ['/', '100000'],
                   'weight': 0.30},
    'scripts': {'crit': SCRIPTS_CRITERIONS,
                'power': 0.916,
                'handle': ['*', '100'],
                'weight': 0.05}
}

results_dict = {
    subsys: {
        'total_rating': 0,
        'print_dict': {}
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

        _model = MathModel(type='ratio')

        for test, config in criterions.items():
            iterations = list(dates[subsystem][test].keys())
            
            print(
            f"""
                {test},
                iterations={iterations},
                values={[dates[subsystem][test][i]['value'] for i in iterations]},
                weight={config['weight']},
                negative={config['negative']},
                reference={config['reference']}
            """)

            _model.add_criterion(str(test),
                                iterations=iterations,
                                values=[dates[subsystem][test][i]['value'] for i in iterations],
                                weight=config['weight'],
                                negative=config['negative'],
                                reference=config['reference'])

        if power_calc:
            fpower = _model.calc_power()
            fpower_dict[subsystem] = fpower['power']
            print(fpower_dict)

            return fpower_dict

        result, crit = _model.total_rating() 
        #print(crit)

        #total_rating = round((result['total_rating']), 3)
        total_rating = round(result, 3)
        print(f"{subsystem} power: {fp}")
        print(f"{subsystem} total_rating: {total_rating}")

        if handle:
            if handle[0] == '/':
                handle_total_rating = (float(total_rating) / int(handle[1]))
            elif handle[0] == '*':
                handle_total_rating = (float(total_rating) * int(handle[1]))

            return round(float(handle_total_rating), 1)
        
        return total_rating, crit


    def start_subsystem_calc(self, power_calc=None):
        for subsystem, values in SUBSYSTEM_DATES.items():
            results_dict[subsystem]['total_rating'], results_dict[subsystem]['print_dict'] = self.subsystem_index_calculator(criterions=values['crit'], 
                                                                                                                             subsystem=subsystem, 
                                                                                                                             power=values['power'],
                                                                                                                             power_calc=power_calc)
            
            results_dict[subsystem]['print_dict'] = self.subsystem_dict_handle(results_dict[subsystem]['print_dict'])


        print(results_dict)
        
        return results_dict


    def subsystem_dict_handle(self, data):
        """
        Преобразователь словаря для любой подсистемы
        """
        result_dict = {}
        
        for test_name, test_data in data.items():
            if 'lat_ctx' in test_name:
                safe_name = 'lat_ctx'
            else:
                safe_name = test_name.replace(' ', '_').replace('-', '_')
                while '__' in safe_name:
                    safe_name = safe_name.replace('__', '_')
            
            result_dict[f"{safe_name}_result"] = test_data['result']
            result_dict[f"{safe_name}_guideline"] = test_data['baseline']
            result_dict[f"{safe_name}_ratio"] = test_data['ratio']
        
        return result_dict


    def total_index_calculator(self, 
                               power_calc: bool = False):
        
        if power_calc:
            return print(f"\n\n{self.start_subsystem_calc(power_calc=True)}")

        subsystem_dates = self.start_subsystem_calc()
        system_info = system.get_system_info()

        # Взвешенное среднее геометрическое
        weighted_geo_mean = 1
        total_weight = 0
        
        for subsystem, values in SUBSYSTEM_DATES.items():
            rating = subsystem_dates[subsystem]['total_rating']
            weighted_geo_mean *= rating ** values['weight']
            total_weight += values['weight']
        
        if total_weight != 1.0:
            weighted_geo_mean = weighted_geo_mean ** (1 / total_weight)
        
        geo_mean = weighted_geo_mean  

        print(TOTAL_TEMPLATE_COLOR.format(test="TEST", 
                                          source="BENCH",
                                          guideline="GUIDELINE", 
                                          result="RESULT",
                                          ratio="RATIO",
                                          kernel=subsystem_dates['kernel']['total_rating'],
                                          processes_ipc=subsystem_dates['processes_ipc']['total_rating'],
                                          filesystem=subsystem_dates['filesystem']['total_rating'],
                                          scripts=subsystem_dates['scripts']['total_rating'],
                                          total=geo_mean,
                                          **system_info,
                                          **results_dict['kernel']['print_dict'],
                                          **results_dict['processes_ipc']['print_dict'],
                                          **results_dict['filesystem']['print_dict'],
                                          **results_dict['scripts']['print_dict']))
        
        



ic = IndexCalculator()
ic.total_index_calculator()



# TODO
# Повысить уровень общего лога до INFO и всю отладку убрать в debug
# прогнать результаты


