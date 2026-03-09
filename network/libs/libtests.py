import re
import json
import numpy as np

from allta import Libvirt, LibvirtManager, SystemCommands

# Uncomment if use Total rating
# from allta import Criterion, MathModels

from pathlib import Path
from time import sleep
from os import path

from net_conf import (USERNAME, 
                      PASSWORD, 
                      VM_OS_INFO_PATH, 
                      BASE_PATH,
                      IOF_OFF_PATH,
                      IOF_ON_PATH,
                      IOF_ON_NAME,
                      IOF_OFF_NAME,
                      ITERATIONS,
                      IOF_RESULTS)


class CreateVM:
    def __init__(
        self,
        rc_name: str = "",
        testdir: str = "",
        kernel: str = "",
        vm_count: int = 0,
        vcpu: int = 0,
        ram: int = 0,
    ):

        self.provider = Libvirt()

        self.rc_name = rc_name
        self.kernel = kernel

        self.vm_count = vm_count
        self.vcpu = vcpu
        self.ram = ram

        self.vms_date_save_path = f"{BASE_PATH}/vms_dates.json"
        self.vms_data = {}
        self.vms = []
        self.vms_group = {}

        self.testdir = testdir

    def prepare_vms(self):
        print("\n\n\nПроверяем существование ВМ\n\n\n")
        if Path(self.vms_date_save_path).is_file():
            print("\n\n\nВМ существуют, восстанавливаем в состояние выполненного provison\n\n\n")
            self.vms_data = LibvirtManager.Vm.load_vms_data(
                save_path=self.vms_date_save_path
            )
            self.vms = list(self.vms_data.keys())
            self.vms_group = {
                "all": self.vms,
            }
            LibvirtManager.Snapshot.revert(vms=self.vms, snapshot_name="provision")
            LibvirtManager.Vm.start(vms=self.vms)
            print("\n\n\nОжидаем 90 секунд для включения ВМ\n\n\n")
            sleep(90)
            self.provider.check(vms=self.vms, vms_dates=self.vms_data)
            print("\n\n\nВМ успешно восстановлены продолжаем тест\n\n\n")
            return 0

        else:
            print("\n\n\nВМ не найдены, создаем\n\n\n")
            self.vms = [f"testvm{i}" for i in range(1, int(self.vm_count) + 1)]
            VMS_DATES = {
                testvm: {"host-port": "22", "cpu": str(self.vcpu), "ram": str(self.ram)}
                for testvm in self.vms
            }
            if isinstance(self.provider, Libvirt):
                VERSION_OS = ".".join(self.rc_name.split(".")[:2])
                self.provider.prepare()
                if VERSION_OS == "1.7":
                    self.vms_data = self.provider.build(
                        box="xfs.1.7.5.o", 
                        rc=self.rc_name,
                        vms=self.vms, 
                        vms_dates=VMS_DATES, 
                        kernel=self.kernel
                    )
                elif VERSION_OS == "1.8":
                    self.vms_data = self.provider.build(
                        box="xfs.1.8.1.o",
                        rc=self.rc_name,
                        vms=self.vms,
                        vms_dates=VMS_DATES,
                        kernel=self.kernel
                    )
            print("\n\n\nВм созданы\n\n\n")
            print("\n\n\nПроверям доступность ВМ\n\n\n")
            self.provider.check(self.vms, self.vms_data)
            print("\n\n\nВМ доступны\n\n\n")
            print("\n\n\nВыполняем provision\n\n\n")
            self.vms_group = {
                "all": self.vms,
            }
            LibvirtManager.Vm.save_vms_data(
                vms_dates=VMS_DATES, save_path=self.vms_date_save_path
            )

            provision_path = "/home/u/provision.sh"
            scp_provision = {
                "g_all": [
                    {
                        "mode": "push",
                        "path_host": f"{self.testdir}/provision/provision.sh",
                        "path_vm": provision_path,
                    },
                ]
            }
            self.provider.scp(
                scp_settings=scp_provision,
                vms_dates=self.vms_data,
                vms_groups=self.vms_group,
                username=USERNAME,
                password=PASSWORD,
            )

            execute_provision = {
                "g_all": {
                    "chmod_provision": {
                        "command": f"sudo chmod +x {provision_path}",
                        "signal set": "1",
                    },
                    "run_provision": {
                        "command": f"sudo bash {provision_path}",
                        "signal set": "2",
                        "signal get": "1",
                    },
                    "reboot": {
                        "command": "",
                        "signal get": "2",
                    },
                }
            }
            self.provider.execute(
                commands=execute_provision,
                vms_dates=self.vms_data,
                vms_groups=self.vms_group,
                username=USERNAME,
                password=PASSWORD,
            )
            print("\n\n\nProvision выполнен")

            # Disable 6 down string to prod (slowed test)
            print("\n\n\nДелаем снимок для быстрого дебага\n\n\n")
            LibvirtManager.Vm.stop(self.vms)
            LibvirtManager.Snapshot.create(vms=self.vms, snapshot_name="provision")
            LibvirtManager.Vm.start(self.vms)
            sleep(90)
            print("\n\n\nСнимок создан, ВМ созданы и к выполнению теста готовы\n\n\n")

    def vms_destroy(self):
        print("\n\n\nВыключаем ВМ:\n\n\n")
        LibvirtManager.Vm.stop(vms=self.vms)


class NetworkLoad(CreateVM):  # In vm work allta_cli!
    def start_test(self):
        """
        testvm1 - server
        testvm2 - client
        """
        
        print ("\n\n\nНачинаем выполнение теста\n\n\n")
        print ("\n\n\nВыполнение подготовки к тесту\n\n\n")
        # Подготовка к выполнению теста
        print("\n\n\nПодготовка завершена\n\n\n")

        print("\n\n\n Настраиваем сеть  \n\n\n")

        LibvirtManager.Vm.stop(vms=self.vms)
        print(SystemCommands.check_output_command('sudo sed -i \'s#<forward mode="nat"/>#<forward mode="none"/>#\' "/vms/network.xml"'))
        print(SystemCommands.check_output_command("sudo virsh net-destroy test"))
        print(SystemCommands.check_output_command("sudo virsh --connect qemu:///system net-create /vms/network.xml"))

        LibvirtManager.Vm.start(vms=self.vms)
        sleep(90)

        print("\n\n\n Сеть настроена  \n\n\n")

        
        init_on_free_off = {
            'g_all': {
                'init_on_free_off': {
                    'command': r"""sudo sed -i -E 's/^(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*)"/\1 init_on_free=off"/' /etc/default/grub""",
                    'signal set': 'sed command',
                },
                'update grub': {
                    'command': 'sudo update-grub',
                    'signal get': 'sed command',
                    'signal set': 'update',
                },
                'reboot': {
                    'signal get': 'update',
                },
            },
        }

        iperf_start = {
            'testvm1': {
                'start_iperf_server': {
                    'command': 'iperf -s',
                    'nowait': True,
                    'nowait_mode': 'continue'
                },
            },
        }

        iperf_load_iof_on = {
            'testvm2': {
                'wait_server': {
                    'command': 'sleep 5',
                    'signal set': 'sleep',
                },
                'iperf_load': {
                    'command': f'iperf -c {self.vms_data['testvm1']['ip_bridge']} >> {IOF_ON_PATH}',
                    'signal get': 'sleep',
                    'signal set': '',
                },
            },
        }

        iperf_load_iof_off = {
            'testvm2': {
                'wait_server': {
                    'command': 'sleep 5',
                    'signal set': 'sleep',
                },
                'iperf_load': {
                    'command': f'iperf -c {self.vms_data['testvm1']['ip_bridge']} >> {IOF_OFF_PATH}',
                    'signal get': 'sleep',
                    'signal set': '',
                },
            },
        }

        
        print("\n\n\nЗапускаем тест c init_on_free=on")

        self.provider.execute(commands=iperf_start, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        for i in range(ITERATIONS):
            print(f'Итерация №{i}')
            self.provider.execute(commands=iperf_load_iof_on, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        
        print("\n\n\nЗапускаем тест c init_on_free=off")
        print('Отключение опции init_on_free')
        self.provider.execute(commands=init_on_free_off, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        self.provider.execute(commands=iperf_start, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)        
        print('Итерация №{i}')
        for i in range(ITERATIONS):
            self.provider.execute(commands=iperf_load_iof_off, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
                

            
        scp_get_result = {
            "testvm2": [
                {
                    "mode": "pull",
                    "path_host": f'{BASE_PATH}/{IOF_ON_NAME}', 
                    "path_vm": IOF_ON_PATH, 
                },
                {
                    "mode": "pull",
                    "path_host": f'{BASE_PATH}/{IOF_OFF_NAME}', 
                    "path_vm": IOF_OFF_PATH, 
                },
            ]
        }
        self.provider.scp(scp_settings=scp_get_result, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)            

        print(f"\n\n\nТест завершен")

        

    def results_processing(self):
        iof_on_mean = 'empty'
        iof_off_mean = 'empty'
        difference = 'empty'

        def mean_calculate(array: np.array):
            valid_values_percent = 30 #Лимит группы по количеству элементов, принимаемой к расчетам, в %
            percent_limit = 50 #Лимит отклонения, в %

            def check_value(value, all_values, percent_limit):
                diffs = np.abs((all_values - value) / value * 100)
                return np.sum(diffs <= percent_limit) >= len(all_values) / 2


            valid_values = [value for value in array if check_value(value, array, percent_limit)]
            novalid_values = [value for value in array if value not in valid_values]
            print('Используемые в расчетах значения:', valid_values)
            print('Отсеянные значения:', novalid_values)

            if len(valid_values) >= len(array) * valid_values_percent / 100:
                mean_cleaned = np.mean(valid_values)
                print(f"Среднее значение без учета аномалий: {int(mean_cleaned) / 2**20} Mbits/sec")
                return int(mean_cleaned) / 2**20
            else:
                print('Нет подходящих групп значений для расчета среднего')
                return 'NaN'

         # Обработка результатов
        print("\n\n\nОбработка результатов")

        if path.exists(f'{BASE_PATH}/{IOF_ON_NAME}'):
            with open(f'{BASE_PATH}/{IOF_ON_NAME}') as r:
                iof_on = r.readlines()
        else: print(f'\n\nОшибка:\nФайл не найден: "{BASE_PATH}/{IOF_ON_NAME}"')

        if path.exists(f'{BASE_PATH}/{IOF_OFF_NAME}'):
            with open(f'{BASE_PATH}/{IOF_OFF_NAME}') as r:
                iof_off = r.readlines()
        else: print(f'\n\nОшибка:\nФайл не найден: "{BASE_PATH}/{IOF_OFF_NAME}"')

        re_pattern = re.compile(r'\b\d+(?:\.\d+)?\s*(?:K|M|G)?bits?/sec\b')

        if iof_on:
            iof_on_results = [match for line in iof_on for match in re_pattern.findall(line)]
            print(f'\nНайденные значения iof_on:\n{iof_on_results}')

            iof_on_dict = dict([value.split(' ') for value in iof_on_results])
            print(iof_on_dict)

            iof_on_bits = [round(float(key)) * 2**(30 if value == 'Gbits/sec' else 20 if value == 'Mbits/sec' else 10)
                        for key, value in iof_on_dict.items()]
            print(f'Найденные значения в битах:\n{iof_on_bits}')

            iof_on_bits_array = np.array(iof_on_bits)
            print(f'Numpy массив:\n{iof_on_bits_array}')

            iof_on_mean = mean_calculate(iof_on_bits_array)

        if iof_off:
            iof_off_results = [match for line in iof_off for match in re_pattern.findall(line)]
            print(f'\nНайденные значения iof_off:\n{iof_off_results}')

            iof_off_dict = dict([value.split(' ') for value in iof_off_results])
            print(iof_off_dict)

            iof_off_bits = [round(float(key)) * 2**(30 if value == 'Gbits/sec' else 20 if value == 'Mbits/sec' else 10)
                        for key, value in iof_off_dict.items()]
            print(f'Найденные значения в битах:\n{iof_off_bits}')

            iof_off_bits_array = np.array(iof_off_bits)
            print(f'Numpy массив:\n{iof_off_bits_array}')

            iof_off_mean = mean_calculate(iof_off_bits_array)

        difference = iof_off_mean / iof_on_mean * 100 -100
        print(f'Разница {round(difference, 1)}%')

        iof_results_dict = {
            'init_on_free_ON': iof_on_mean,
            'init_on_free_OFF': iof_off_mean,
            'difference': round(difference, 1)
        }
        print(iof_results_dict)

        with open(IOF_RESULTS, 'w') as w:
            w.write(json.dumps(iof_results_dict))

        print('\n\nЗабираем данные о ОС с ВМ\n')
        # Get vm params (av, kernel), params create in provision
        scp_vm_params = {
            "testvm1": [
                {
                    "mode": "pull",
                    "path_host": VM_OS_INFO_PATH + "/av.txt",
                    "path_vm": "/home/u/av.txt",
                },
                {
                    "mode": "pull",
                    "path_host": VM_OS_INFO_PATH + "/kernel.txt",
                    "path_vm": "/home/u/kernel.txt",
                },                
            ]
        }
        self.provider.scp(
            scp_settings=scp_vm_params,
            vms_dates=self.vms_data,
            vms_groups=self.vms_group,
            username=USERNAME,
            password=PASSWORD,
        )
        print ("results gets")
        pass
