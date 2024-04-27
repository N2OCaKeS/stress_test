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
from virt_conf import VM_INFONAME, VM_KERNEL, BLOCK_SIZE, FILE_SIZE, FIOVERS_17x, \
                      FIOVERS_18x, TEMPLATE_PATH, FIO_PATH, UB_ARHIVE, STEP, \
                      LOW_COPIES, HIGH_COPIES
from time import sleep



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
        astra_config_url = 'http://bendiks.devos.astralinux.ru/rest/api/get-astra-config'
        response_ac = requests.get(astra_config_url)
        if response_ac.status_code == 200:
            with open('astra-config.json', 'wb') as acb:
                acb.write(response_ac.content)
        else:
            print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

        with open('astra-config.json', 'r') as r:
            dates = loads(r.read())

        def __box_wrapper(box):
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
                          if '1.7.5.o' in str(i):
                            box_name = i['1.7.5.o'][0]
                            box_url = i['1.7.5.o'][1]
                    elif str(box).startswith('1.8'):
                        if '1.8.0.o' in str(i):
                            box_name = i['1.8.0.o'][0]
                            box_url = i['1.8.0.o'][1]
            
            return box_name, box_url

        if not os.path.isdir(self.testdir):
            os.mkdir(self.testdir)

        # add_box
        box_name, box_url = __box_wrapper(self.rc_name)
        cmd(f'vagrant box add --provider virtualbox {box_name} {box_url}')
        cmd(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')

        # add define pool
        try:
            cmd('virsh pool-define-as --name default --type dir --target /var/lib/libvirt/images')
            cmd('virsh pool-autostart default')
            cmd('virsh pool-start default')
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

        # create_vm
        print('UPDATE={} BOX_URL={} RC={} KERNEL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(box_name,
                                                                                                                 box_url,
                                                                                                                 self.rc_name,
                                                                                                                 self.kernel,
                                                                                                                 self.vm_count,
                                                                                                                 self.vcpu,
                                                                                                                 self.ram))
        cmd('UPDATE={} BOX_URL={} RC={} KERNEL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(box_name,
                                                                                                               box_url,
                                                                                                               self.rc_name,
                                                                                                               self.kernel,
                                                                                                               self.vm_count,
                                                                                                               self.vcpu,
                                                                                                               self.ram))

        print('\nWait reboot VMs 180s...\n')
        sleep(180)



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
        
        [
            create_remote_file(local_file_path='cpu_load', 
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
            get_remote_file(remote_file_path=f'/home/av.txt',
                            local_file_path=f'{self.testdir}/{VM_INFONAME}',
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



class FlexibleIOTester(CreateVM):
    def __init__(self, 
                 rc_vbox=None, 
                 testdir=None, 
                 vm_count=None, 
                 kernel=None, 
                 vcpu=None, 
                 ram=None,
                 iodepth=None,
                 vm_num=None):
        super().__init__(rc_vbox, testdir, vm_count, kernel, vcpu, ram)

        self.vms = [f'testvm{number}' for number in range(1, self.vm_count + 1)]
        self.vm_num = vm_num
        self.user = 'vagrant'
        self.password = 'vagrant'
        self.check_vm_ip = "virsh domifaddr {} | awk '{{print $4}}' | tail -n 2"
        self.vg_destroy = 'vagrant destroy {}'
        self.destroy = 'virsh destroy {}'
        self.undefine = 'virsh undefine {}'
        self.block_size = f'--bs={BLOCK_SIZE}'
        self.io_depth = f'--iodepth={iodepth}'
        self.file_size = f'--size={FILE_SIZE}'
        self.results_file_name = f'/home/{self.user}/test_{iodepth}.txt'
        self.fio_hardend = 'fio --rw=randrw --ioengine=libaio --name=test '
        self.fio_cmd = self.fio_hardend + f'{self.block_size} {self.io_depth} {self.file_size} > ' + self.results_file_name
        self.fb_cmd = 'sudo apt-get install -fy'
        self.iodepth = iodepth
        if str(rc_vbox).startswith('1.7'):
            self.fio_version = FIOVERS_17x
        elif str(rc_vbox).startswith('1.8'):
            self.fio_version = FIOVERS_18x
        else: self.fio_version = FIOVERS_18x
        


    def start_test(self):
        self.vm_dates = {
             vm:{
                'ip': check_output_command(self.check_vm_ip.format(vm)).split('/')[0],
                'login':f'{self.user}',
                'password':f'{self.password}'
                } for vm in self.vms}
        print(f'VM dates is:\n{self.vm_dates}')

        #Send & install fio pkg
        create_remote_file(local_file_path=f'{FIO_PATH}/{self.fio_version}', 
                            remote_file_path=f'/home/{self.user}/{self.fio_version}', 
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 

        send_remote_command(command=f'uname -r > /home/{self.user}/kernel.txt',
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 

        send_remote_command(command=f'sudo dpkg -i /home/{self.user}/{self.fio_version}',
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 

        send_remote_command(command=self.fb_cmd,
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 

        send_remote_command(command=f'sudo dpkg -i /home/{self.user}/{self.fio_version}',
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 

        #exec test cmd
        send_remote_command(command=self.fio_cmd,
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 

        get_remote_file(remote_file_path=self.results_file_name,
                        local_file_path=f'{self.testdir}/result_testvm{self.vm_num}.info',
                        ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                        user=self.user, 
                        password=self.password) 

        get_remote_file(remote_file_path=f'/home/av.txt',
                        local_file_path=f'{self.testdir}/{VM_INFONAME}',
                        ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                        user=self.user, 
                        password=self.password) 

        get_remote_file(remote_file_path=f'/home/{self.user}/kernel.txt',
                        local_file_path=f'{self.testdir}/{VM_KERNEL}',
                        ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                        user=self.user, 
                        password=self.password) 


    # Run before end general test, else every VM will shutdown 300 sec before reboot
    def vms_destroy(self):
        try:
            [
                cmd(f'virsh dumpxml {vm_name}') for vm_name in self.vms
            ]
            [
                cmd(self.destroy.format(vm_name)) for vm_name in self.vms
            ]
            [
                cmd(self.undefine.format(vm_name)) for vm_name in self.vms
            ]
            cmd('rm -rf .vagrant')
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

    
    def results_processing(self):
        results_dir = self.testdir
        files = os.listdir(results_dir)

        with open(f'{results_dir}/result_testvm{self.vm_num}.info', 'r') as r:
            text = r.readlines()

        values = {
            'read_iops':[text[i].split(' ') for i in range(len(text)) if 'read' in text[i] and 'IOPS' in text[i]],
            'read_clat':[text[i+2].split(' ') for i in range(len(text)) if 'read' in text[i] and 'IOPS' in text[i]],
            'write_iops':[text[i].split(' ') for i in range(len(text)) if 'write' in text[i] and 'IOPS' in text[i]],
            'write_clat':[text[i+2].split(' ') for i in range(len(text)) if 'write' in text[i] and 'IOPS' in text[i]]
        }

        def __k_parser(value:str):
            if value.endswith('k,'):
                return str(float(re.findall(r'\d+.\d+?', value)[0]) * 1000).replace('.0', '')
            else: return re.findall(r'\d+.\d+?', value)[0]

        dates = {
            'write':{'IOPS':__k_parser(values['write_iops'][0][3]),
                    'Latency/avg':__k_parser(values['write_clat'][0][8])},
            'read':{'IOPS':__k_parser(values['read_iops'][0][3]),
                    'Latency/avg':__k_parser(values['read_clat'][0][8])}
        }

        print(dates)


        df = pd.DataFrame(dates).T
        print(df)
        df.to_html(f'{TEMPLATE_PATH}/result_testvm_{self.iodepth}.html')


        def __cleared():
                results_file = [f for f in files if not f.endswith('html') and not f.endswith('info')]
                return results_file
        [cmd(f'rm -r {results_dir}/{file}') for file in __cleared()]
        


class UnixBench(CreateVM):
    def __init__(self, 
                 rc_vbox=None, 
                 testdir=None, 
                 vm_count=None, 
                 kernel=None, 
                 vcpu=None, 
                 ram=None,
                 vm_num=None):
        super().__init__(rc_vbox, testdir, vm_count, kernel, vcpu, ram)

        self.vms = [f'testvm{number}' for number in range(1, self.vm_count + 1)]
        self.vm_num = vm_num
        self.user = 'vagrant'
        self.password = 'vagrant'
        self.check_vm_ip = "virsh domifaddr {} | awk '{{print $4}}' | tail -n 2"
        self.vg_destroy = 'vagrant destroy {}'
        self.destroy = 'virsh destroy {}'
        self.undefine = 'virsh undefine {}'
        self.run_cmd = './Run ' + ' '.join([f'-c {copy}' for copy in range(LOW_COPIES, HIGH_COPIES, STEP)])


    def start_test(self):
        self.vm_dates = {
             vm:{
                'ip': check_output_command(self.check_vm_ip.format(vm)).split('/')[0],
                'login':f'{self.user}',
                'password':f'{self.password}'
                } for vm in self.vms}
        print(f'VM dates is:\n{self.vm_dates}')

        #Send & install unixbench zip
        create_remote_file(local_file_path=UB_ARHIVE, 
                           remote_file_path=f'/home/{self.user}/unixbench.zip', 
                           ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                           user=self.user, 
                           password=self.password) 
        
        send_remote_command(command=f'uname -r > /home/{self.user}/kernel.txt',
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 
        
        send_remote_command(command=f'cd /home/{self.user} && unzip unixbench.zip',
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 

        send_remote_command(command=f'cd /home/{self.user}/UnixBench && {self.run_cmd}',
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 
        
        send_remote_command(command=f'cd /home/{self.user}/UnixBench/results && zip -r result.zip *',
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 

        get_remote_file(remote_file_path=f'/home/{self.user}/UnixBench/results/result.zip',
                        local_file_path=f'{self.testdir}/result_testvm{self.vm_num}.info',
                        ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                        user=self.user, 
                        password=self.password) 

        get_remote_file(remote_file_path=f'/home/av.txt',
                        local_file_path=f'{self.testdir}/{VM_INFONAME}',
                        ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                        user=self.user, 
                        password=self.password) 

        get_remote_file(remote_file_path=f'/home/{self.user}/kernel.txt',
                        local_file_path=f'{self.testdir}/{VM_KERNEL}',
                        ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                        user=self.user, 
                        password=self.password) 


