import os
import pandas as pd
import re
import numpy as np

def results_processing():
    results_dir = './'
    files = os.listdir(results_dir)
    vms_name_files = sorted([f.strip('.txt').strip('result').strip('_') 
                            for f in files if re.match(r'result_testvm(\d+)?\.txt', f)],
                            key=lambda x: int(re.findall(r'\d+', x)[0]))
    print(vms_name_files)

    with open(f'./host_results.txt', 'r') as r:
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

    #print(main_dates)
        
    print('\nMean steal time')
    steal_time = [float(data.replace(',', '.')) for data in main_dates[i]['steal_time'].values() 
                  for i in vms_name_files]
    mean_steal_time = '%.2f' % np.mean(steal_time)
    print(mean_steal_time)
    df_mean_steal_time = pd.DataFrame({'Mean steal time':mean_steal_time}, index=[''])
    print(df_mean_steal_time)
    
    print('\nMean instructions')
    instructions = [float(main_dates[i]['instructions']) for i in vms_name_files]
    mean_instructions = '%.1f' % np.mean(instructions)
    print(mean_instructions)
    df_mean_instructions = pd.DataFrame({'Mean instructions':mean_instructions}, index=[''])
    print(df_mean_instructions)

    df_instructions = pd.DataFrame(index=['instructions'])
    for name in vms_name_files:
        df_instructions.at['instructions', name] = main_dates[name]['instructions']

    print("\nVMs instructions count")
    print(df_instructions)


    df_list = []
    df_host = pd.DataFrame(main_dates['host'], index=['value']).T
    df_host.index.name = 'time'
    df_list.append(df_host) 

    for vm in vms_name_files:
        globals()[f'df_{vm}'] = pd.DataFrame(main_dates[vm]['steal_time'], index=['value']).T
        globals()[f'df_{vm}'].index.name = 'time'
        df_list.append(globals()[f'df_{vm}'])

    df = pd.concat(df_list, axis=1, keys=['host'] + vms_name_files)
    df.sort_index(inplace=True)

    print("\nCPU util & VMs steal time")
    print(df)

    df_instructions.to_html(f'{results_dir}/vms_instructions.html')
    df.to_html(f'{results_dir}/vms_steal_time.html')
    df_mean_instructions.to_html(f'{results_dir}/mean_instructions.html', index=False)
    df_mean_steal_time.to_html(f'{results_dir}/mean_steal_time.html', index=False)


results_processing()
