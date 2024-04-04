from libs.virtlib import (check_output_command, 
                          cmd, 
                          send_remote_command,
                          create_remote_file,
                          get_remote_file)
from threading import Thread
import requests
import os
import datetime
import re
import pandas as pd
from json import loads
import numpy as np



class StealTime:
    def __init__(self,
                 rc_vbox=None,
                 vm_count=None,
                 testdir=None,
                 load_type=None,
                 kernel=None):
        
        self.kernel = kernel
        self.load_type = load_type
        self.vm_count = vm_count
        self.testdir = testdir
        self.rc_name = rc_vbox
        self.vms = [f'testvm{number}' for number in range(1, self.vm_count + 1)]
        self.check_vm_ip = "virsh domifaddr {} | awk '{{print $4}}' | tail -n 2"
        self.set_exec_bit = 'sudo chmod +x /home/{}/cpu_load'
        self.run_test = 'cd /home/{} && sudo ./cpu_load'
        self.power_off = 'virsh destroy {}'
        self.user = 'vagrant'
        self.password = 'vagrant'
        self.stop_host_monitor = False
        self.load_host_monitor_results = {}
        self.stop_host_monitor = False
                

        
    def prepare_and_start(self):
        astra_config_url = 'http://bendiks.devos.astralinux.ru/rest/api/get-astra-config'
        response_ac = requests.get(astra_config_url)
        if response_ac.status_code == 200:
            with open('astra-config.json', 'wb') as acb:
                acb.write(response_ac.content)
        else:
            print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

        with open('astra-config.json', 'r') as r:
            dates = loads(r.read())

        def box_wrapper(box):
            true_key = False
            box_name = ''
            box_url = ''
            for i in dates['astra-version']['vagrant_box']:
                if box in str(i):
                    for key in i.keys():
                        if str(key).endswith('o'):
                            true_key = key
                            box_name = i[true_key][0]
                            box_url = i[true_key][1]             
                    
            if true_key == False:
                for i in dates['astra-version']['vagrant_box']:
                    if str(box).startswith('1.7'):
                          if '1.7.1.o' in str(i):
                            box_name = i['1.7.1.o'][0]
                            box_url = i['1.7.1.o'][1]
                    elif str(box).startswith('1.8'):
                        if '1.8.0.o' in str(i):
                            box_name = i['1.8.0.o'][0]
                            box_url = i['1.8.0.o'][1]
            
            return box_name, box_url

        if not os.path.isdir(self.testdir):
            os.mkdir(self.testdir)

        # add_box
        box_name, box_url = box_wrapper(self.rc_name)
        cmd(f'vagrant box add --provider virtualbox {box_name} {box_url}')
        cmd(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')

        # create_vm
        cmd(f'''UPDATE={box_name} BOX_URL={box_url} RC={self.rc_name} KERNEL={self.kernel} 
                COUNT={self.vm_count} vagrant up --provider=libvirt''')
        
        self.vm_dates = {
             vm:{
                'ip': check_output_command(self.check_vm_ip.format(vm)).split('/')[0],
                'login':f'{self.user}',
                'password':f'{self.password}'
                } for vm in self.vms}
        print(f'VM dates is:\n{self.vm_dates}')

        try: 
            [
                create_remote_file(local_file_path='./libs/cpu_load', 
                                remote_file_path=f'/home/{self.user}/cpu_load', 
                                ip=self.vm_dates[vm]['ip'], 
                                user=self.user, 
                                password=self.password) 
                                for vm in self.vms
            ]
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

        try:
            [
                send_remote_command(command=self.set_exec_bit.format(self.user),
                                    ip=self.vm_dates[vm]['ip'], 
                                    user=self.user, 
                                    password=self.password) 
                                    for vm in self.vms
            ]
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')


        def load_host_monitor():
            def __check_cpu_load():
                    comm = """top -bn1 | grep '%Cpu' | tail -1 | awk '{gsub(",",".",$8); printf "%s", 100-$8 "%"}'"""
                    result = check_output_command(comm)
                    return result
            
            while not self.stop_host_monitor:
                self.load_host_monitor_results[datetime.datetime.now().strftime('%H:%M:%S')] = __check_cpu_load()
                    
            with open(f'{self.testdir}/host_results.txt', 'w') as w:
                w.write(f'{self.load_host_monitor_results}\n')


        def run_vm_test(vm):
            try:
                send_remote_command(command=self.run_test.format(self.user),
                                    ip=self.vm_dates[vm]['ip'], 
                                    user=self.user, 
                                    password=self.password)        
            except Exception as e:
                print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

        thread_monitor = Thread(target=load_host_monitor)
        thread_monitor.start()

        threads = []
        for vm in self.vms:
            thread = Thread(target=run_vm_test, args=(vm,))
            thread.start()
            threads.append(thread)
        [thread.join() for thread in threads]
        self.stop_host_monitor = True

        if thread_monitor.is_alive():
            thread_monitor.join()
            

        try: 
            [
                get_remote_file(remote_file_path=f'/home/{self.user}/result.txt',
                                local_file_path=f'{self.testdir}/result_{vm}.txt',
                                ip=self.vm_dates[vm]['ip'], 
                                user=self.user, 
                                password=self.password) 
                                for vm in self.vms
            ]
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

        try: 
            [
                get_remote_file(remote_file_path=f'/home/av.txt',
                                local_file_path=f'{self.testdir}/av.txt',
                                ip=self.vm_dates[vm]['ip'], 
                                user=self.user, 
                                password=self.password) 
                                for vm in self.vms
            ]
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

        try: 
            [
                get_remote_file(remote_file_path=f'/home/{self.user}/kernel.txt',
                                local_file_path=f'{self.testdir}/kernel.txt',
                                ip=self.vm_dates[vm]['ip'], 
                                user=self.user, 
                                password=self.password) 
                                for vm in self.vms
            ]
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')


    # Run before end general test, else every VM will shutdown 300 sec before reboot
    def vms_destroy(self):
        try:
            [
                cmd(self.power_off.format(vm_name)) for vm_name in self.vms
            ]
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')


    def results_processing(self):
        results_dir = self.testdir
        files = os.listdir(results_dir)
        vms_name_files = sorted([f.strip('.txt').strip('result').strip('_') 
                                for f in files if re.match(r'result_testvm(\d+)?\.txt', f)],
                                key=lambda x: int(re.findall(r'\d+', x)[0]))
        print(f'\nUsed VMs:\n{vms_name_files}')
        if len(vms_name_files) != self.vm_count:
            print('Wrong VMs count been created. Aborted')
            print(f'Expected: {self.vm_count}, Received: {len(vms_name_files)}')
            return -1


        with open(f'{results_dir}/host_results.txt', 'r') as r:
            host_data = r.read()

        result = str(host_data.strip().strip('{,}').replace("'", "").split("% ")).strip("'[]").split(", ")
        main_dates = {
            'host': {key.split(': ', 1)[0]: key.split(': ', 1)[1] for key in result if len(key) > 10}
        }

        for i in vms_name_files:
            with open(f'{results_dir}/result_{i}.txt', 'r') as r:
                data = r.readlines()

            main_dates[i] = {}
            main_dates[i]['instructions'] = data[0].split(' ')[3]
            main_dates[i]['steal_time'] = {
                item.split(' ')[2]: item.split(' ')[4].strip() for item in data[1::]
            }


        print('\nMean steal time')
        steal_time = [float(data.replace(',', '.')) for data in main_dates[i]['steal_time'].values() 
                    for i in vms_name_files]
        mean_steal_time = '%.2f' % np.mean(steal_time)
        print(mean_steal_time)
        df_mean_steal_time = pd.DataFrame({'Mean steal time':mean_steal_time}, index=[''])
        
        print('\nMean instructions')
        instructions = [float(main_dates[i]['instructions']) for i in vms_name_files]
        mean_instructions = '%.1f' % np.mean(instructions)
        print(mean_instructions)
        df_mean_instructions = pd.DataFrame({'Mean instructions':mean_instructions}, index=[''])
        

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


        df_instructions.to_html(f'{results_dir}/{self.load_type}_vms_instructions.html')
        df.to_html(f'{results_dir}/{self.load_type}_vms_steal_time.html')
        df_mean_instructions.to_html(f'{results_dir}/{self.load_type}_mean_instructions.html', index=False)
        df_mean_steal_time.to_html(f'{results_dir}/{self.load_type}_mean_steal_time.html', index=False)


        def cleared():
            results_file = [f for f in files if not f.endswith('html')]
            return results_file
        [cmd(f'rm -r {results_dir}/{file}') for file in cleared()]

        return len(vms_name_files)


