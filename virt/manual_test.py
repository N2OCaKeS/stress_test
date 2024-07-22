from libs.virtlib import (check_output_command, 
                          cmd, 
                          send_remote_command,
                          create_remote_file,
                          get_remote_file)
import os
import datetime
import re
import pandas as pd
import numpy as np
from threading import Thread
import argparse
from virt_conf import VM_KERNEL, LOW, HIGH, ST_vCPU, ST_RAM


#TODO in new VM
#add vagrant user in boxes, openssh, ssh key, guests


class CreateVM:
    def __init__(self,
                 rc_vbox=None,
                 testdir=None,
                 vm_count=None,
                 kernel=None,
                 vcpu=None,
                 ram=None):
        
        self.rc_name = rc_vbox
        self.testdir = testdir
        self.kernel = kernel
        self.vm_count = vm_count
        self.vcpu = vcpu
        self.ram = ram

    def prepare_vms(self):
        box_name = 'debian'
        box_url = 'ftp://10.177.103.10/boxes/box/debian.box'

        cmd(f'vagrant box add --force --provider virtualbox {box_name} {box_url}')
        cmd(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')

        # add define pool
        try:
            cmd('virsh pool-define-as --name default --type dir --target /var/lib/libvirt/images')
            cmd('virsh pool-autostart default')
            cmd('virsh pool-start default')
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

        # create_vm
        print('UPDATE={} BOX_URL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(box_name,
                                                                                                 box_url,                                                                                                            
                                                                                                 self.vm_count,
                                                                                                 self.vcpu,
                                                                                                 self.ram))
        cmd('UPDATE={} BOX_URL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(box_name,
                                                                                               box_url,
                                                                                               self.vm_count,
                                                                                               self.vcpu,
                                                                                               self.ram))
        
        if not os.path.isdir(self.testdir):
            os.mkdir(self.testdir)
        


class StealTime(CreateVM):
    def __init__(self,
                 vm_count=None,
                 testdir=None,
                 load_type=None,
                 vcpu=None,
                 ram=None,
                 rc_vbox=None,
                 kernel=None):
        super().__init__(rc_vbox, testdir, vm_count, kernel, vcpu, ram)
        
        self.load_type = load_type
        self.vm_count = vm_count
        self.testdir = testdir
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


    def start_test(self):
        self.vm_dates = {
             vm:{
                'ip': check_output_command(self.check_vm_ip.format(vm)).split('/')[0],
                'login':f'{self.user}',
                'password':f'{self.password}'
                } for vm in self.vms}
        print(f'VM dates is:\n{self.vm_dates}')
        
        print(f'Env path: {os.getcwd()}')

        [
            create_remote_file(local_file_path=f'{os.getcwd()}/libs/cpu_load', 
                               remote_file_path=f'/home/{self.user}/cpu_load', 
                               ip=self.vm_dates[vm]['ip'], 
                               user=self.user, 
                               password=self.password) 
                               for vm in self.vms
        ]
        [
            send_remote_command(command=self.set_exec_bit.format(self.user),
                                ip=self.vm_dates[vm]['ip'], 
                                user=self.user, 
                                password=self.password) 
                                for vm in self.vms
        ]


        def __load_host_monitor():
            def __check_cpu_load():
                    comm = """top -bn1 | grep '%Cpu' | tail -1 | awk '{gsub(",",".",$8); printf "%s", 100-$8 "%"}'"""
                    result = check_output_command(comm)
                    return result
            
            while not self.stop_host_monitor:
                self.load_host_monitor_results[datetime.datetime.now().strftime('%H:%M:%S')] = __check_cpu_load()
                    
            with open(f'{self.testdir}/host_results.txt', 'w') as w:
                w.write(f'{self.load_host_monitor_results}\n')


        def __run_vm_test(vm):
            try:
                send_remote_command(command=self.run_test.format(self.user),
                                    ip=self.vm_dates[vm]['ip'], 
                                    user=self.user, 
                                    password=self.password)        
            except Exception as e:
                print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

        thread_monitor = Thread(target=__load_host_monitor)
        thread_monitor.start()

        threads = []
        for vm in self.vms:
            thread = Thread(target=__run_vm_test, args=(vm,))
            thread.start()
            threads.append(thread)
        [thread.join() for thread in threads]
        self.stop_host_monitor = True

        if thread_monitor.is_alive():
            thread_monitor.join()
            

        [
            get_remote_file(remote_file_path=f'/home/{self.user}/result.txt',
                            local_file_path=f'{self.testdir}/result_{vm}.txt',
                            ip=self.vm_dates[vm]['ip'], 
                            user=self.user, 
                            password=self.password) 
                            for vm in self.vms
        ]
        [
            get_remote_file(remote_file_path=f'/home/{self.user}/kernel.txt',
                            local_file_path=f'{self.testdir}/{VM_KERNEL}',
                            ip=self.vm_dates[vm]['ip'], 
                            user=self.user, 
                            password=self.password) 
                            for vm in self.vms
        ]



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
            results_file = [f for f in files if not f.endswith('html') and not f.endswith('info')]
            return results_file
        [cmd(f'rm -r {results_dir}/{file}') for file in cleared()]

        return len(vms_name_files)        
    



parser = argparse.ArgumentParser()
parser.add_argument('-box',
                    action='store',
                    choices=['debian',
                             'rhel',
                             'redos',
                             'alt'],
                    required=True,
                    help='vbox name',
                    dest='BOX')
args = parser.parse_args()

####################################
#Start test
####################################
box = args.BOX

st_no_errors = True
low_load_test = StealTime(rc_vbox=box,
                          vm_count=LOW,
                          testdir='TEST',
                          load_type='low',
                          vcpu=ST_vCPU,
                          ram=ST_RAM)

high_load_test = StealTime(rc_vbox=box,
                           vm_count=HIGH,
                           testdir='TEST',
                           load_type='high',
                           vcpu=ST_vCPU,
                           ram=ST_RAM)

low_load_test.prepare_vms()
low_load_test.start_test()
low_load_test.vms_destroy()
if low_load_test.results_processing() == LOW:
    print('Low load test successfully done')
else: 
    st_no_errors = False
    print('Low load test fail')

high_load_test.prepare_vms()
high_load_test.start_test()
high_load_test.vms_destroy()
if high_load_test.results_processing() == HIGH:
    print('High load test successfully done')
else: 
    st_no_errors = False
    print('High load test fail')

if st_no_errors:
    print('No errors')
    
