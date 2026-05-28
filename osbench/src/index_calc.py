import json
import sys

from allta import MathModel
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.conf import RESULTS_MAIN_DIR, KERNEL_CRITERIONS


primer = """
['4', '8', '16']
[664978.4, 692978.0, 716833.0]
syscall,
iterations=['4', '8', '16'],
values=[664978.4, 692978.0, 716833.0],
weight=0.12,
negative=False,
bounds=(0.0, 74000000)

model = MathModel()
model.add_criterion(str(test),
                    iterations=sys_iter,
                    values=sys_val,
                    weight=0.9,
                    negative=False,
                    bounds=(0.0, 7400000))

fixed_power = 0.9996180247850317
result = model.total_rating(power=fixed_power)  

return round(result['total_rating'] / 100)

print(result)
print(result['total_rating'])
print(type(result['total_rating']))
print(round(result['total_rating'], 2) * 10)
"""


with open(f"{RESULTS_MAIN_DIR}/testing_sbsdates.json", "r") as f:
    dates = json.load(f)


def _get_syscall_aggregate(dates) -> dict:

    kernel_dict = dates['kernel']
    syscall = kernel_dict['syscall']
    print(syscall)

    syscall_summ = [sum(round(syscall[conc]['value']) for conc in syscall.keys())]
    print(syscall_summ)

    kernel_dict['syscall'] = {}
    kernel_dict['syscall']['value'] = syscall_summ.pop()
    print(kernel_dict)

    return kernel_dict

sys_iter = list(dates['kernel']['syscall'].keys())
sys_val = [dates['kernel']['syscall'][i]['value'] for i in sys_iter]

print(sys_iter)
print(sys_val)


model = MathModel()
kernel_iteration = 0
#kernel_dict = _get_syscall_aggregate(dates)
for test, conf in KERNEL_CRITERIONS.items():
    kernel_iteration += 1
    print(f"""{test},
               iterations={sys_iter},
               values={sys_val},
               weight={conf['weight']},
               negative={conf['negative']},
               bounds={conf['bounds']}
           """)
model.add_criterion(str(test),
                    iterations=sys_iter,
                    values=sys_val,
                    weight=0.9,
                    negative=False,
                    bounds=(0.0, 7400000))


    

fixed_power = 0.9996180247850317
result = model.total_rating(power=fixed_power)  

print(result)
print(result['total_rating'])
print(type(result['total_rating']))
print(round(result['total_rating'], 2) * 10)