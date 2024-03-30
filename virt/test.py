import json
import pandas as pd
import os
from virt_conf import TESTDIR
import re

results_dir = './'
files = os.listdir(results_dir)
vms_name_files = [f.strip('.txt').strip('result').strip('_') 
                  for f in files if re.match(r'result_testvm(\d+)?\.txt', f)]
print(vms_name_files)



with open('./host_results.txt', 'r') as r:
    host_data = r.read()


result = str(host_data.strip().strip('{,}').replace("'", "").split("% ")).strip("'[]").split(", ")
main_dates = {
    'host': {key.split(': ', 1)[0]: key.split(': ', 1)[1] for key in result if len(key) > 10}
}

#print(result)
#print(main_dates)

for i in vms_name_files:
    with open(f'./result_{i}.txt', 'r') as r:
        data = r.readlines()

    main_dates[i] = {}
    main_dates[i]['instructions'] = data[0].split(' ')[3]
    main_dates[i]['steal_time'] = {
        item.split(' ')[2]: item.split(' ')[4].strip() for item in data[1::]
    }


print(main_dates)

# TODO сделать DF для instructions
#df = pd.DataFrame({'value':'NaN'}, index=['instructions'])
#df_host.at['instructions', 'testvm1'] = main_dates['testvm1']['instructions']

df_list = []

df_host = pd.DataFrame(main_dates['host'], index=['value']).T
df_host.index.name = 'time'
df_list.append(df_host) 


for vm in vms_name_files:
    globals()[f'df_{vm}'] = pd.DataFrame(main_dates[vm]['steal_time'], index=['value']).T
    globals()[f'df_{vm}'].index.name = 'time'
    df_list.append(globals()[f'df_{vm}'])

df = pd.concat(df_list, axis=1, keys=['host'] + vms_name_files)




print(df)

