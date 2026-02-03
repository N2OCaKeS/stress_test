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
                      LOW_COPIES, HIGH_COPIES, REPORT_PATH, UB_RESULT_HTML, UB_RESULTS, \
                      VM_RESULTS_PATH
from time import sleep
from allta import Libvirt, Criterion, MathModels



class CreateVM:
    def __init__(self,
                 special_att=None,
                 rc_vbox=None,
                 testdir=None,
                 vm_count=None,
                 kernel=None,
                 vcpu=None,
                 ram=None,
                 mode='o',
                 provider=Libvirt()):

        """
        :param special_att: Специальный атрибут для передачи индивидуальных параметров
        :param rc_vbox: Параметр rc_vbox, значение по умолчанию None.
        :param testdir: Параметр testdir, значение по умолчанию None.
        :param vm_count: Количество виртуальных машин, значение по умолчанию None.
        :param kernel: Параметр kernel, значение по умолчанию None.
        :param vcpu: Количество виртуальных процессоров, значение по умолчанию None.
        :param ram: Объём оперативной памяти, значение по умолчанию None.
        :param mode: Режим, значение по умолчанию None.
        :param provider: Используемый провайдер
        """

        self.mode = mode
        self.rc_name = rc_vbox
        self.testdir = testdir
        self.kernel = kernel
        self.vm_count = vm_count
        self.vcpu = vcpu
        self.ram = ram
        self.provider = provider
        self.special_att = special_att

    def prepare_vms(self):
        VERSION_OS = '.'.join(self.rc_name.split('.')[:2])

        VMS = [f'testvm{i}' for i in range(1, int(self.vm_count) + 1)]  # Краткий список ВМ

        VMS_DATES = { # Полный список ВМ
            testvm: {'host-port': '22',
                     'cpu': str(self.vcpu),
                     'ram': str(self.ram)}
            for testvm in VMS
        }

        if isinstance(self.provider, Libvirt):
            self.provider.prepare()
            if VERSION_OS == '1.7':
                if self.special_att == 'fio':
                    VMS_DATES = self.provider.build(f'vm_station1.7', self.rc_name, VMS, VMS_DATES)
                else:
                    VMS_DATES = self.provider.build(f'1.7.5.{self.mode}', self.rc_name, VMS, VMS_DATES)
            elif VERSION_OS == '1.8':
                if self.special_att == 'fio':
                    VMS_DATES = self.provider.build(f'vm_station1.8', self.rc_name, VMS, VMS_DATES)
                else:
                    VMS_DATES = self.provider.build(f'1.8.1.{self.mode}', self.rc_name, VMS, VMS_DATES)

        self.provider.check(VMS, VMS_DATES)
        print(f'VMS DATES:\n{VMS_DATES}')

        self.provider.scp(
            scp_settings={
                'g_VMS': [
                    {
                        'mode': 'push', 
                        'path_host': '/home/u/git/stress_test/virt/provision/env_provision.sh', 
                        'path_vm': '/home/u/env_provision.sh'
                    }
                ]
            },
            vms_dates=VMS_DATES,
            vms_groups={
                'VMS':VMS
            }
        )
        print(f'<{str(self.provider.scp.__name__).upper()}> block done ' + ('*' * 50))

        self.provider.execute(
            commands={
                'g_VMS':{
                    'provision':{
                        'command':f"sudo bash /home/u/env_provision.sh {self.rc_name} {self.kernel}",
                        'signal set': 'provision', 
                        'signal get': ''
                    },
                    'reboot':{ 
                        'signal set': '', 
                        'signal get': ['provision']
                    }
                }
            },
            vms_dates=VMS_DATES,
            vms_groups={
                'VMS':VMS
            }
        )
        print(f'<{str(self.provider.execute.__name__).upper()}> block done ' + ('*' * 50))




        # astra_config_url = 'http://allta.devos.astralinux.ru/rest/api/get-box-config'
        # response_ac = requests.get(astra_config_url)
        # if response_ac.status_code == 200:
        #     with open('box-config.json', 'wb') as acb:
        #         acb.write(response_ac.content)
        # else:
        #     print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

        # with open('box-config.json', 'r') as r:
        #     dates = loads(r.read())

        # def __box_wrapper(box, mode):
        #     true_key = False
        #     box_name = ''
        #     box_url = ''
        #     for i in dates['vagrant_box']:
        #         if box in str(i):
        #             for key in i.keys():
        #                 if str(key).endswith(mode):
        #                     true_key = key
        #                     box_name = i[true_key][0]
        #                     box_url = i[true_key][1]             
                    
        #     if true_key == False:
        #         for i in dates['vagrant_box']:
        #             if str(box).startswith('1.7'):
        #                   if f'1.7.5.{mode}' in str(i):
        #                     box_name = i[f'1.7.5.{mode}'][0]
        #                     box_url = i[f'1.7.5.{mode}'][1]
        #             elif str(box).startswith('1.8'):
        #                 if f'1.8.1.{mode}' in str(i):
        #                     box_name = i[f'1.8.1.{mode}'][0]
        #                     box_url = i[f'1.8.1.{mode}'][1]
            
        #     return box_name, box_url

        # if not os.path.isdir(self.testdir):
        #     os.mkdir(self.testdir)

        # # add_box
        # box_name, box_url = __box_wrapper(self.rc_name, self.mode)
        # print(f'vagrant box add --provider virtualbox {box_name} {box_url}')
        # cmd(f'vagrant box add --provider virtualbox {box_name} {box_url}')
        # print(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')
        # cmd(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')

        # # add define pool
        # try:
        #     cmd('virsh pool-define-as --name default --type dir --target /var/lib/libvirt/images')
        #     cmd('virsh pool-autostart default')
        #     cmd('virsh pool-start default')
        # except Exception as e:
        #     print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

        # # create_vm
        # print('UPDATE={} BOX_URL={} RC={} KERNEL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(box_name,
        #                                                                                                          box_url,
        #                                                                                                          self.rc_name,
        #                                                                                                          self.kernel,
        #                                                                                                          self.vm_count,
        #                                                                                                          self.vcpu,
        #                                                                                                          self.ram))
        # cmd('UPDATE={} BOX_URL={} RC={} KERNEL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(box_name,
        #                                                                                                        box_url,
        #                                                                                                        self.rc_name,
        #                                                                                                        self.kernel,
        #                                                                                                        self.vm_count,
        #                                                                                                        self.vcpu,
        #                                                                                                        self.ram))

        # print('\nWait reboot VMs 180s...\n')
        # sleep(180)



class StealTime(CreateVM):
    def __init__(self,
                 vm_count=None,
                 testdir=None,
                 load_type=None,
                 vcpu=None,
                 ram=None,
                 rc_vbox=None,
                 kernel=None,
                 mode='o'):
        super().__init__(rc_vbox=rc_vbox, 
                         testdir=testdir, 
                         vm_count=vm_count, 
                         kernel=kernel, 
                         vcpu=vcpu, 
                         ram=ram, 
                         mode=mode)
        
        self.load_type = load_type
        self.vm_count = vm_count
        self.testdir = testdir
        self.vms = [f'testvm{number}' for number in range(1, self.vm_count + 1)]
        self.check_vm_ip = "virsh domifaddr {} | awk '{{print $4}}' | tail -n 2"
        self.set_exec_bit = 'sudo chmod +x /home/{}/cpu_load'
        self.run_test = 'cd /home/{} && sudo ./cpu_load'
        self.power_off = 'virsh destroy {}'
        self.user = 'u'
        self.password = '1'
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
                 vm_num=None,
                 special_att='fio'):
        super().__init__(rc_vbox=rc_vbox, 
                         testdir=testdir, 
                         vm_count=vm_count, 
                         kernel=kernel, 
                         vcpu=vcpu, 
                         ram=ram, 
                         special_att=special_att)

        self.vms = [f'testvm{number}' for number in range(1, int(vm_count) + 1)]
        self.vm_num = vm_num
        self.user = 'u'
        self.password = '1'
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
        if self.fio_version == FIOVERS_17x:
            create_remote_file(local_file_path=f'{FIO_PATH}/{self.fio_version}', 
                                remote_file_path=f'/home/{self.user}/{self.fio_version}', 
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
        else:
            send_remote_command(command=f'sudo apt-get install fio -y',
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 


        send_remote_command(command=f'uname -r > /home/{self.user}/kernel.txt',
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
        
class LargeFio(CreateVM):
    def __init__(self,
                 rc_vbox=None,
                 testdir=None,
                 vm_count=1,
                 kernel=None,
                 vcpu=None,
                 ram=None,
                 mode='o',
                 ):
        super().__init__(rc_vbox=rc_vbox,
                         testdir=testdir,
                         vm_count=vm_count or 1,
                         kernel=kernel,
                         vcpu=vcpu,
                         ram=ram,
                         mode=mode)
        self.vm_count = vm_count
        self.vms = [f'testvm{number}' for number in range(1, self.vm_count + 1)]
        self.rc_vbox = rc_vbox
        self.user = 'u'
        self.password = '1'
        self.check_vm_ip = "virsh domifaddr {} | awk '{{print $4}}' | tail -n 2"
        self.destroy = 'virsh destroy {}'
        self.undefine = 'virsh undefine {}'
        self.fb_cmd = 'sudo apt-get install -fy'
        self.disk = '/dev/sda'
        if str(rc_vbox).startswith('1.7'):
            self.fio_version = FIOVERS_17x
        elif str(rc_vbox).startswith('1.8'):
            self.fio_version = FIOVERS_18x
        else: 
            self.fio_version = FIOVERS_18x


    def start_test(self):
        self.vm_dates = {
            vm: {
                'ip': check_output_command(self.check_vm_ip.format(vm)).split('/')[0],
                'login': f'{self.user}',
                'password': f'{self.password}'
            } for vm in self.vms
        }
        add_disk_path = "/vms/largefio"
        print(f'VM dates is:\n{self.vm_dates}')
        
        print(f'Preparing large fio disk at {add_disk_path}')
        print(check_output_command(f"sudo parted -s {self.disk} mklabel gpt"))
        print(check_output_command(f"sudo parted -s {self.disk} mkpart primary ext4 0% 100%"))
        print(check_output_command(f"sudo partprobe {self.disk}"))
        print(check_output_command("sudo udevadm settle"))
        print(check_output_command(f"sudo mkfs.ext4 -F {self.disk}1")     )
        print(check_output_command(f"sudo mkdir {add_disk_path}"))
        print(check_output_command(f"sudo mount {self.disk}1 {add_disk_path}"))
        print(check_output_command(f"sudo chmod 777 {add_disk_path}"))
        
        print(f'Creating large fio disk image in {add_disk_path}')
        print(check_output_command(f"sudo virsh --connect qemu:///system pool-define-as fio dir --target {add_disk_path}"))
        print(check_output_command("sudo virsh --connect qemu:///system pool-build fio"))
        print(check_output_command("sudo virsh --connect qemu:///system pool-start fio"))
        print(check_output_command("sudo virsh --connect qemu:///system pool-autostart fio"))
        print(check_output_command("sudo virsh --connect qemu:///system pool-refresh fio"))
        print(check_output_command(f"qemu-img create -f qcow2 -o cluster_size=1M,extended_l2=on,lazy_refcounts=on {add_disk_path}/largefio1M.qcow2 1T"))
        print(check_output_command(f"qemu-img create -f qcow2 -o cluster_size=64k,extended_l2=on,lazy_refcounts=on {add_disk_path}/largefio64k.qcow2 1T"))

        print(check_output_command("sudo virsh destroy testvm1"))
        print(check_output_command("sudo virsh dumpxml  testvm1 > /tmp/testvm1.xml"))
        print(check_output_command(
            f"awk -v img='{add_disk_path}/largefio1M.qcow2' -v dev='vdb' "
            "'/<\\/devices>/{"
            "print \"  <disk type=\\\"file\\\" device=\\\"disk\\\">\";"
            "print \"    <driver name=\\\"qemu\\\" type=\\\"qcow2\\\" cache=\\\"none\\\" io=\\\"native\\\" discard=\\\"unmap\\\"/>\";"
            "print \"    <source file=\\\"\" img \"\\\"/>\";"
            "print \"    <target dev=\\\"\" dev \"\\\" bus=\\\"virtio\\\"/>\";"
            "print \"    <serial>largefio-cluster-1M</serial>\";"
            "print \"  </disk>\""
            "}"
            "{print}' /tmp/testvm1.xml > /tmp/testvm1.new.xml"
        ))
        print(check_output_command(
            f"awk -v img='{add_disk_path}/largefio64k.qcow2' -v dev='vdc' "
            "'/<\\/devices>/{"
            "print \"  <disk type=\\\"file\\\" device=\\\"disk\\\">\";"
            "print \"    <driver name=\\\"qemu\\\" type=\\\"qcow2\\\" cache=\\\"none\\\" io=\\\"native\\\" discard=\\\"unmap\\\"/>\";"
            "print \"    <source file=\\\"\" img \"\\\"/>\";"
            "print \"    <target dev=\\\"\" dev \"\\\" bus=\\\"virtio\\\"/>\";"
            "print \"    <serial>largefio-cluster-64K</serial>\";"            
            "print \"  </disk>\""
            "}"
            "{print}' /tmp/testvm1.new.xml > /tmp/testvm1.new2.xml"
        ))        
        print(check_output_command("sudo virsh define /tmp/testvm1.new2.xml"))
        print(check_output_command("sudo virsh start testvm1"))
        sleep(60)
        # print(check_output_command(f"virsh attach-disk {self.vms[0]} {add_disk_path}/largefio.qcow2 vdb --type disk --sourcetype file --targetbus virtio --driver qemu --subdriver qcow2 --cache none --io native --live"))
        print('Disk attached, preparing inside VM')
        send_remote_command(command="sudo dd if=/dev/urandom of=/dev/vdb bs=1G count=1024 oflag=direct status=progress", ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        send_remote_command(command="sudo dd if=/dev/urandom of=/dev/vdc bs=1G count=1024 oflag=direct status=progress", ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)        
        send_remote_command(command='sudo uname -r > /home/u/kernel.txt && sudo chmod 777 /home/u/kernel.txt', ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)

        print('Install fio package')
        if self.fio_version == FIOVERS_17x:
            create_remote_file(local_file_path=f'{FIO_PATH}/{self.fio_version}', 
                                remote_file_path=f'/home/{self.user}/{self.fio_version}', 
                                ip=self.vm_dates['testvm1']['ip'], 
                                user=self.user, 
                                password=self.password) 
            
            send_remote_command(command=f'sudo dpkg -i /home/{self.user}/{self.fio_version}',
                                ip=self.vm_dates['testvm1']['ip'], 
                                user=self.user, 
                                password=self.password) 

            send_remote_command(command=self.fb_cmd,
                                ip=self.vm_dates['testvm1']['ip'], 
                                user=self.user, 
                                password=self.password) 

            send_remote_command(command=f'sudo dpkg -i /home/{self.user}/{self.fio_version}',
                                ip=self.vm_dates['testvm1']['ip'], 
                                user=self.user, 
                                password=self.password)
        else:
            send_remote_command(command='sudo apt-get install fio -y',
                            ip=self.vm_dates['testvm1']['ip'], 
                            user=self.user, 
                            password=self.password) 

        print('Send load script')
        create_remote_file(local_file_path=f"{FIO_PATH}/largefio.sh", remote_file_path="/home/u/largefio.sh", ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        send_remote_command(command="sudo chmod +x /home/u/largefio.sh", ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        print('Start load script')

        send_remote_command(command="sudo /home/u/largefio.sh read vdb > /home/u/largefio_read1M.txt" , ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        send_remote_command(command="sudo /home/u/largefio.sh write vdb > /home/u/largefio_write1M.txt" , ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        send_remote_command(command="sudo chmod 777 /home/u/largefio_write1M.txt" , ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        send_remote_command(command="sudo chmod 777 /home/u/largefio_read1M.txt" , ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)    
        send_remote_command(command="sudo /home/u/largefio.sh read vdc > /home/u/largefio_read64k.txt" , ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        send_remote_command(command="sudo /home/u/largefio.sh write vdc > /home/u/largefio_write64k.txt" , ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        send_remote_command(command="sudo chmod 777 /home/u/largefio_write64k.txt" , ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        send_remote_command(command="sudo chmod 777 /home/u/largefio_read64k.txt" , ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)            
    
    def results_processing(self):
        print('Get results')

        local_read_64k  = f"{self.testdir}/largefio_read_64k.txt"
        local_write_64k = f"{self.testdir}/largefio_write_64k.txt"
        local_read_1m   = f"{self.testdir}/largefio_read_1M.txt"
        local_write_1m  = f"{self.testdir}/largefio_write_1M.txt"

        get_remote_file("/home/u/largefio_read64k.txt",  local_read_64k,  ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        get_remote_file("/home/u/largefio_write64k.txt", local_write_64k, ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        get_remote_file("/home/u/largefio_read1M.txt",   local_read_1m,   ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        get_remote_file("/home/u/largefio_write1M.txt",  local_write_1m,  ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)

        get_remote_file('/home/av.txt',       f'{self.testdir}/{VM_INFONAME}', ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)
        get_remote_file('/home/u/kernel.txt', f'{self.testdir}/{VM_KERNEL}',   ip=self.vm_dates['testvm1']['ip'], user=self.user, password=self.password)

        # --- парсим READ 64k ---
        read_64k = []
        with open(local_read_64k, "r") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                size = parts[0]
                try:
                    val = float(parts[1])
                except ValueError:
                    continue
                read_64k.append((size, val))

        # --- парсим READ 1M ---
        read_1m = []
        with open(local_read_1m, "r") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                size = parts[0]
                try:
                    val = float(parts[1])
                except ValueError:
                    continue
                read_1m.append((size, val))

        # --- парсим WRITE 64k ---
        write_64k = []
        with open(local_write_64k, "r") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                size = parts[0]
                try:
                    val = float(parts[1])
                except ValueError:
                    continue
                write_64k.append((size, val))

        # --- парсим WRITE 1M ---
        write_1m = []
        with open(local_write_1m, "r") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                size = parts[0]
                try:
                    val = float(parts[1])
                except ValueError:
                    continue
                write_1m.append((size, val))

        df_read_64k  = pd.DataFrame(read_64k,  columns=["size", "64k"])
        df_read_1m   = pd.DataFrame(read_1m,   columns=["size", "1M"])
        df_write_64k = pd.DataFrame(write_64k, columns=["size", "64k"])
        df_write_1m  = pd.DataFrame(write_1m,  columns=["size", "1M"])

        df_read  = pd.merge(df_read_64k,  df_read_1m,  on="size", how="outer", sort=False)
        df_write = pd.merge(df_write_64k, df_write_1m, on="size", how="outer", sort=False)

        for df in (df_read, df_write):
            df.sort_values(
                by="size",
                key=lambda s: s.astype(str).str.upper().apply(
                    lambda x: float(x[:-1]) * (1024 if x.endswith("T") else 1)
                ),
                inplace=True
            )
            df.reset_index(drop=True, inplace=True)
        size_key = "1T"
        read_1t_1m = df_read.loc[
            df_read["size"].astype(str).str.upper() == size_key, "1M"
        ].dropna().tolist()
        write_1t_1m = df_write.loc[
            df_write["size"].astype(str).str.upper() == size_key, "1M"
        ].dropna().tolist()

        if not read_1t_1m or not write_1t_1m:
            print(
                "LargeFio warning: missing 1T results for 1M cluster "
                f"(read={len(read_1t_1m)}, write={len(write_1t_1m)})."
            )

        criterions = [
            Criterion(name="read 1mb 1t", values=read_1t_1m, weight=0.5, lower_bound=0, upper_bound=200000, sign=1),
            Criterion(name="write 1mb 1t", values=write_1t_1m, weight=0.5, lower_bound=0, upper_bound=200000, sign=1),
        ]
        total_rating, _ = MathModels.total_rating(criteria=criterions)
        total_rating_display = round(total_rating * 1000)
        print(f"LargeFio total rating: {total_rating_display}")
        with open(f'{TEMPLATE_PATH}/largefio_total_rating.txt', 'w') as w:
            w.write(str(total_rating))


        # --- сохраняем HTML ---
        df_read.to_html(f"{TEMPLATE_PATH}/largefio_read_results.html", index=False)
        df_write.to_html(f"{TEMPLATE_PATH}/largefio_write_results.html", index=False)

        print("READ table:")
        print(df_read)
        print("WRITE table:")
        print(df_write)

        # Clean raw txt copies once converted to html
        for p in (local_read_64k, local_write_64k, local_read_1m, local_write_1m):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass

    def vms_destroy(self):
        try:
            [
                cmd(self.destroy.format(vm_name)) for vm_name in self.vms
            ]
            [
                cmd(self.undefine.format(vm_name)) for vm_name in self.vms
            ]
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

class UnixBench(CreateVM):
    def __init__(self, 
                 rc_vbox=None, 
                 testdir=None, 
                 vm_count=None, 
                 kernel=None, 
                 vcpu=None, 
                 ram=None,
                 vm_num=None):
        super().__init__(rc_vbox=rc_vbox, 
                         testdir=testdir, 
                         vm_count=vm_count, 
                         kernel=kernel, 
                         vcpu=vcpu, 
                         ram=ram)

        self.vms = [f'testvm{number}' for number in range(1, self.vm_count + 1)]
        self.vm_num = vm_num
        self.user = 'u'
        self.password = '1'
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
        
        send_remote_command(command=f'sudo apt-get install zip unzip -y',
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
        
        send_remote_command(command=f'chmod -R +x /home/{self.user}/UnixBench',
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 

        #Start test
        send_remote_command(command=f'cd /home/{self.user}/UnixBench && {self.run_cmd}',
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 
        
        send_remote_command(command=f'cd /home/{self.user}/UnixBench/results && zip -r result.zip *',
                            ip=self.vm_dates[f'testvm{self.vm_num}']['ip'], 
                            user=self.user, 
                            password=self.password) 

        get_remote_file(remote_file_path=f'/home/{self.user}/UnixBench/results/result.zip',
                        local_file_path=f'{self.testdir}/result_testvm{self.vm_num}.zip',
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
        arh_name = f'result_testvm{self.vm_num}.zip'
        cmd(f'cd {REPORT_PATH} && unzip {arh_name}')

        files = os.listdir(REPORT_PATH)
        file_name = [name for name in files if all(x not in name for x in ['log', 'zip', 'info', 'html'])]
        print(f'File name: {file_name}')
                   
        with open(f'{REPORT_PATH}/{file_name[0]}', 'r') as r:
            text = r.readlines()

        keys = [' '.join(line.split(' ')[5:7]) for line in text if 'running' in line]
        values = [line.split(' ')[-1].strip() for line in text if 'Score' in line]
        results = {k: v for k, v in zip(keys, values)}
        print(f'Keys: {keys}')
        print(f'Values: {values}')
        print(f'Results: {results}')

        if not os.path.isdir(UB_RESULTS):
            os.mkdir(UB_RESULTS)

        df = pd.DataFrame(results, index=['Total score']).T
        df.to_html(f'{UB_RESULTS}/{UB_RESULT_HTML}')



class PingPong(CreateVM):
    def __init__(self, 
                 rc_vbox=None, 
                 testdir=None, 
                 vm_count=None, 
                 kernel=None, 
                 vcpu=None, 
                 ram=None):
        super().__init__(rc_vbox=rc_vbox, 
                         testdir=testdir, 
                         vm_count=vm_count, 
                         kernel=kernel, 
                         vcpu=vcpu, 
                         ram=ram)

        self.vms = [f'testvm{number}' for number in range(1, self.vm_count + 1)]
        self.user = 'u'
        self.password = '1'
        self.check_vm_ip = "virsh domifaddr {} | awk '{{print $4}}' | tail -n 2"
        self.vg_destroy = 'vagrant destroy {}'
        self.destroy = 'virsh destroy {}'
        self.undefine = 'virsh undefine {}'


    def start_test(self):
        self.vm_dates = {
             vm:{
                'ip': check_output_command(self.check_vm_ip.format(vm)).split('/')[0],
                'login':f'{self.user}',
                'password':f'{self.password}'
                } for vm in self.vms}
        print(f'VM dates is:\n{self.vm_dates}')

        #Send pingpong script & start test
        create_remote_file(local_file_path=f'{os.getcwd()}/libs/ping_pong.py', 
                           remote_file_path=f'/home/{self.user}/ping_pong.py', 
                           ip=self.vm_dates['testvm1']['ip'], 
                           user=self.user, 
                           password=self.password) 
        
        send_remote_command(command=f'chmod -R +x /home/{self.user}/ping_pong.py',
                            ip=self.vm_dates['testvm1']['ip'], 
                            user=self.user, 
                            password=self.password) 
        
        send_remote_command(command=f'uname -r > /home/{self.user}/kernel.txt',
                            ip=self.vm_dates['testvm1']['ip'], 
                            user=self.user, 
                            password=self.password)
        
        #Start test
        send_remote_command(command=f'python3 /home/{self.user}/ping_pong.py',
                            ip=self.vm_dates['testvm1']['ip'], 
                            user=self.user, 
                            password=self.password)
    
        get_remote_file(remote_file_path='/home/u/results',
                        local_file_path=f'{self.testdir}/{VM_RESULTS_PATH}',
                        ip=self.vm_dates['testvm1']['ip'], 
                        user=self.user, 
                        password=self.password)
        
        get_remote_file(remote_file_path=f'/home/av.txt',
                        local_file_path=f'{self.testdir}/{VM_INFONAME}',
                        ip=self.vm_dates['testvm1']['ip'], 
                        user=self.user, 
                        password=self.password) 

        get_remote_file(remote_file_path=f'/home/{self.user}/kernel.txt',
                        local_file_path=f'{self.testdir}/{VM_KERNEL}',
                        ip=self.vm_dates['testvm1']['ip'], 
                        user=self.user, 
                        password=self.password) 
        

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
