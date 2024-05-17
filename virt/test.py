import re
import pandas as pd
from virt_conf import STEP, LOW_COPIES, HIGH_COPIES, UB_RESULT_HTML
from libs.virtlib import cmd
import os

path = '/home/u/git/stress_test/virt/test_results/'
arh_name = 'result_testvm1.zip'
cmd(f'cd {path} && unzip {arh_name}')

files = os.listdir(path)
file_name = [name for name in files if all(x not in name for x in ['log', 'zip', 'info', 'log'])]
             

with open(f'{path}{file_name[0]}', 'r') as r:
    text = r.readlines()


keys = [' '.join(line.split(' ')[5:7]) for line in text if 'running' in line]
values = [line.split(' ')[-1].strip() for line in text if 'Score' in line]

results = {k: v for k, v in zip(keys, values)}

print(results)


df = pd.DataFrame(results, index=['Total score']).T

print(df)  

df.to_html(UB_RESULT_HTML)



#s = './Run ' + ' '.join([f'-c {copy}' for copy in range(LOW_COPIES, HIGH_COPIES, STEP)])

#print(s)