import re
import pandas as pd
from virt_conf import STEP, LOW_COPIES, HIGH_COPIES


with open('test.txt', 'r') as r:
    text = r.readlines()



results = {
    ' '.join(key.split(' ')[5:7]): value.split(' ')[-1] 
    for value in text if 'Score' in value for key in text if 'running' in key
    }

print(results)


df = pd.DataFrame(results, index=['Total score']).T

print(df)  


s = './Run ' + ' '.join([f'-c {copy}' for copy in range(LOW_COPIES, HIGH_COPIES, STEP)])

print(s)