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
                      IOF_RESULTS,
                      DHCP_SERVER_VM,
                      DHCP_SUBNET,
                      DHCP_NETMASK,
                      DHCP_SERVER_IP,
                      DHCP_CLIENT_IPS,
                      KEA_SERVER_PACKAGES,
                      KEA_CLIENT_PACKAGES,
                      DHCP_CONF_LOCAL_PATH,
                      DHCP_CONF_REMOTE_PATH,
                      DHCP_POOL_START,
                      DHCP_POOL_END,
                      DHCP_LOAD_CLIENT_VM,
                      DHCP_PERFDHCP_RATE,
                      DHCP_PERFDHCP_CLIENT_STEPS,
                      DHCP_PERFDHCP_REMOTE_PATH,
                      DHCP_PERFDHCP_LOCAL_NAME,
                      DHCP_PERFDHCP_STEP_MARKER_PREFIX,
                      DHCP_KEA_STATS_BEFORE_REMOTE,
                      DHCP_KEA_STATS_AFTER_REMOTE,
                      DHCP_KEA_PROC_STATS_REMOTE,
                      DHCP_RESULTS,
                      )


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
            SystemCommands.check_output_command('sudo cp /vms/network.xml.bak /vms/network.xml')
            SystemCommands.check_output_command("sudo virsh net-destroy test")
            SystemCommands.check_output_command("sudo virsh --connect qemu:///system net-create /vms/network.xml")
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
                vms_dates=self.vms_data, save_path=self.vms_date_save_path
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
            SystemCommands.check_output_command('sudo cp /vms/network.xml /vms/network.xml.bak')
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

class Dhcp(CreateVM):
    def start_test(self):
        """
        testvm1 - kea-dhcp4-server (статический IP)
        testvm2 - клиент perfdhcp, получает адрес от kea по MAC-резервации
        """

        print("\n\n\nНачинаем выполнение теста\n\n\n")
        print("\n\n\nВыполнение подготовки к тесту\n\n\n")

        clients = [vm for vm in self.vms if vm != DHCP_SERVER_VM]
        self.vms_group = {
            "all": self.vms,
            "server": [DHCP_SERVER_VM],
            "clients": clients,
        }

        print("\n\n\nСобираем MAC-адреса клиентов для host-reservations в kea\n\n\n")
        mac_pattern = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")
        macs = {}
        for vm in self.vms_group["clients"]:
            output = SystemCommands.check_output_command(f"virsh -c qemu:///system domiflist {vm}")
            match = mac_pattern.search(output)
            if not match:
                raise RuntimeError(f"Не удалось получить MAC-адрес интерфейса ВМ {vm}: {output}")
            macs[vm] = match.group(1)
            print(f"{vm}: {macs[vm]}")

        # Пул целиком пустой: адрес получают только заранее известные MAC из reservations,
        # посторонние DHCPDISCOVER на стенде остаются без ответа
        reservations = [
            {"hw-address": macs[vm], "ip-address": ip}
            for vm, ip in DHCP_CLIENT_IPS.items()
            if vm in macs
        ]

        kea_config = {
            "Dhcp4": {
                "interfaces-config": {
                    "interfaces": ["*"],
                },
                "control-socket": {
                    "socket-type": "unix",
                    "socket-name": "/tmp/kea4-ctrl-socket",
                },
                "lease-database": {
                    "type": "memfile",
                    "persist": True,
                    "name": "/var/lib/kea/kea-leases4.csv",
                },
                "valid-lifetime": 3600,
                "renew-timer": 900,
                "rebind-timer": 1800,
                "subnet4": [
                    {
                        "id": 1,
                        "subnet": DHCP_SUBNET,
                        # Пул под синтетических клиентов perfdhcp, не пересекается
                        # с DHCP_SERVER_IP и MAC-резервациями ниже
                        "pools": [{"pool": f"{DHCP_POOL_START} - {DHCP_POOL_END}"}],
                        "reservations": reservations,
                    }
                ],
                "loggers": [
                    {
                        "name": "kea-dhcp4",
                        "output_options": [{"output": "/var/log/kea/kea-dhcp4.log"}],
                        "severity": "INFO",
                    }
                ],
            }
        }

        with open(DHCP_CONF_LOCAL_PATH, "w") as f:
            json.dump(kea_config, f, indent=2, ensure_ascii=False)

        print("\n\n\nПодготовка завершена\n\n\n")

        # kea ещё не запущен -> testvm1 не может получить адрес по DHCP от себя же,
        # поэтому адрес прибивается статикой в /etc/network/interfaces заранее,
        # пока сервер ещё доступен по старому ip_bridge (DHCP гипервизора)
        print("\n\n\nНастраиваем статический IP на сервере kea\n\n\n")

        static_ip_command = f"""iface=$(ip a | grep '2: ' | awk '{{print$2}}' | tr -d ':' | head -n1)
sudo tee /etc/network/interfaces > /dev/null <<EOF
source /etc/network/interfaces.d/*

auto lo
iface lo inet loopback

auto $iface
iface $iface inet static
    address {DHCP_SERVER_IP}
    netmask {DHCP_NETMASK}
EOF"""

        set_static_ip = {
            DHCP_SERVER_VM: {
                "set_static_ip": {
                    "command": static_ip_command,
                },
            },
        }
        self.provider.execute(commands=set_static_ip, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        print("\n\n\nУстанавливаем kea-dhcp4-server на сервере и kea-common/kea-admin на клиентах\n\n\n")
        install_packages = {}
        if self.rc_name.startswith("1.7"):
            short = ".".join(self.rc_name.split(".")[:3])
            install_packages = {
                "g_server": {
                    "add repo": {
                        "command": f"echo \"deb https://releases.devos.astralinux.ru/frozen/1.7/{short}/EXT_latest/extended-repository 1.7_x86-64 main contrib non-free\" | sudo tee -a /etc/apt/sources.list ",
                        "signal set": "1",
                    },
                    "install_kea_server": {
                        "command": f"sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y {' '.join(KEA_SERVER_PACKAGES)}",
                        "signal get": "1"
                    },
                },
                "g_clients": {
                    "add repo": {
                        "command": f"echo \"deb https://releases.devos.astralinux.ru/frozen/1.7/{short}/EXT_latest/extended-repository 1.7_x86-64 main contrib non-free\" | sudo tee -a /etc/apt/sources.list ",
                        "signal set": "1",
                    },                    
                    "install_kea_clients": {
                        "command": f"sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y {' '.join(KEA_CLIENT_PACKAGES)}",
                        "signal get": "1"
                    },
                },            
            }
        elif self.rc_name.startswith("1.8"):
            install_packages = {
                "g_server": {
                    "install_kea_server": {
                        "command": f"sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y {' '.join(KEA_SERVER_PACKAGES)}",
                    },
                },
                "g_clients": {
                    "install_kea_clients": {
                        "command": f"sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y {' '.join(KEA_CLIENT_PACKAGES)}",
                    },
                },            
            }
        self.provider.execute(commands=install_packages, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        print("\n\n\nПушим сгенерированный kea-dhcp4.conf на сервер\n\n\n")

        scp_conf = {
            DHCP_SERVER_VM: [
                {
                    "mode": "push",
                    "path_host": DHCP_CONF_LOCAL_PATH,
                    "path_vm": "/home/u/kea-dhcp4.conf",
                },
            ],
        }
        self.provider.scp(scp_settings=scp_conf, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        apply_conf = {
            DHCP_SERVER_VM: {
                "install_kea_conf": {
                    "command": (
                        f"sudo mkdir -p $(dirname {DHCP_CONF_REMOTE_PATH}) && "
                        f"sudo mv /home/u/kea-dhcp4.conf {DHCP_CONF_REMOTE_PATH} && "
                        f"sudo chown root:root {DHCP_CONF_REMOTE_PATH}"
                    ),
                },
                "enable_kea": {
                    "command": "sudo systemctl enable kea-dhcp4-server",
                },
            },
        }
        self.provider.execute(commands=apply_conf, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        print("\n\n\n Настраиваем сеть  \n\n\n")

        print("\n\n\nОтключаем DHCP гипервизора: убираем <dhcp> из /vms/network.xml, единственным DHCP-сервером на сегменте остаётся kea\n\n\n")

        LibvirtManager.Vm.stop(vms=self.vms)
        print(SystemCommands.check_output_command('sudo sed -i \'/<dhcp>/,/<\\/dhcp>/d\' "/vms/network.xml"'))
        print(SystemCommands.check_output_command("sudo virsh net-destroy test"))
        print(SystemCommands.check_output_command("sudo virsh --connect qemu:///system net-create /vms/network.xml"))

        print("\n\n\nСтартуем сервер kea первым, ждём загрузки\n\n\n")
        LibvirtManager.Vm.start(vms=[DHCP_SERVER_VM])
        sleep(60)

        # domifaddr больше не увидит адрес сервера (свой DHCP гипервизора отключён) -
        # адрес известен заранее, он же зашит в /etc/network/interfaces сервера
        self.vms_data[DHCP_SERVER_VM]["ip_bridge"] = DHCP_SERVER_IP

        check_kea_active = {
            DHCP_SERVER_VM: {
                "check_kea_active": {
                    "command": "systemctl status kea-dhcp4-server.service | grep active",
                },
            },
        }
        self.provider.execute(commands=check_kea_active, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        print("\n\n\nСтартуем клиентов, ждём получения аренд по DHCP от kea\n\n\n")
        LibvirtManager.Vm.start(vms=self.vms_group["clients"])
        sleep(60)

        # адреса клиентов зарезервированы по MAC в kea-конфиге, поэтому пишем их напрямую,
        # без discovery через virsh domifaddr
        for vm, ip in DHCP_CLIENT_IPS.items():
            self.vms_data[vm]["ip_bridge"] = ip

        print("\n\n\n Сеть настроена  \n\n\n")

        print("\n\n\nПроверяем доступность стенда по SSH на новых адресах\n\n\n")

        self.provider.check(vms=self.vms, vms_dates=self.vms_data)

        print("\n\n\nСтенд для DHCP-теста развёрнут\n\n\n")

        kea_stats_query = 'echo \'{"command": "statistic-get-all", "arguments": {}}\' | sudo socat - UNIX-CONNECT:/tmp/kea4-ctrl-socket'

        print(f"\n\n\nЗапускаем ступенчатую нагрузку perfdhcp при фиксированном rate={DHCP_PERFDHCP_RATE}, шаги по N клиентов: {DHCP_PERFDHCP_CLIENT_STEPS}\n\n\n")

        for step_idx, n_clients in enumerate(DHCP_PERFDHCP_CLIENT_STEPS):
            print(f"\n\n\n=== Шаг {step_idx}: N={n_clients} клиентов ===\n\n\n")

            kea_stats_before = {
                DHCP_SERVER_VM: {
                    "kea_stats_before": {
                        "command": f"{kea_stats_query} > {DHCP_KEA_STATS_BEFORE_REMOTE}",
                    },
                },
            }
            self.provider.execute(commands=kea_stats_before, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

            kea_proc_sampler_start = {
                DHCP_SERVER_VM: {
                    "start_proc_sampler": {
                        "command": f"bash -c 'LC_ALL=C pidstat -u -r -p $(pgrep -x kea-dhcp4) 1 > {DHCP_KEA_PROC_STATS_REMOTE} 2>&1'",
                        "nowait": True,
                        "nowait_mode": "continue",
                    },
                },
            }
            self.provider.execute(commands=kea_proc_sampler_start, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

            perfdhcp_step = {
                DHCP_LOAD_CLIENT_VM: {
                    "perfdhcp_step": {
                        "command": (
                            f"echo '{DHCP_PERFDHCP_STEP_MARKER_PREFIX}{n_clients} ===' >> {DHCP_PERFDHCP_REMOTE_PATH}; "
                            "iface=$(ip a | grep '2: ' | awk '{print$2}' | tr -d ':' | head -n1); "
                            f"sudo perfdhcp -4 -r {DHCP_PERFDHCP_RATE} -R {n_clients} -n {n_clients} "
                            f"-l $iface {DHCP_SERVER_IP} "
                            f">> {DHCP_PERFDHCP_REMOTE_PATH} 2>&1 || true"
                        ),
                    },
                },
            }
            self.provider.execute(commands=perfdhcp_step, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

            kea_proc_sampler_stop = {
                DHCP_SERVER_VM: {
                    "stop_proc_sampler": {
                        # -INT (не -TERM), чтобы pidstat успел дописать
                        "command": "sudo pkill -INT -x pidstat 2>/dev/null || true; sleep 2",
                    },
                },
            }
            self.provider.execute(commands=kea_proc_sampler_stop, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

            kea_stats_after = {
                DHCP_SERVER_VM: {
                    "kea_stats_after": {
                        "command": f"{kea_stats_query} > {DHCP_KEA_STATS_AFTER_REMOTE}",
                    },
                },
            }
            self.provider.execute(commands=kea_stats_after, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

            # kea-статистику и pidstat-семпл этого шага сразу забираем под
            # именем с номером шага - иначе следующий шаг перезапишет remote-файлы
            # до того, как мы их подтянем
            scp_get_step_results = {
                DHCP_SERVER_VM: [
                    {
                        "mode": "pull",
                        "path_host": f"{BASE_PATH}/kea_stats_before_step{step_idx}.json",
                        "path_vm": DHCP_KEA_STATS_BEFORE_REMOTE,
                    },
                    {
                        "mode": "pull",
                        "path_host": f"{BASE_PATH}/kea_stats_after_step{step_idx}.json",
                        "path_vm": DHCP_KEA_STATS_AFTER_REMOTE,
                    },
                    {
                        "mode": "pull",
                        "path_host": f"{BASE_PATH}/kea_proc_stats_step{step_idx}.txt",
                        "path_vm": DHCP_KEA_PROC_STATS_REMOTE,
                    },
                ],
            }
            self.provider.scp(scp_settings=scp_get_step_results, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        print("\n\n\nЗабираем общий файл вывода perfdhcp (все шаги через маркеры)\n\n\n")
        scp_get_perfdhcp = {
            DHCP_LOAD_CLIENT_VM: [
                {
                    "mode": "pull",
                    "path_host": f"{BASE_PATH}/{DHCP_PERFDHCP_LOCAL_NAME}",
                    "path_vm": DHCP_PERFDHCP_REMOTE_PATH,
                },
            ],
        }
        self.provider.scp(scp_settings=scp_get_perfdhcp, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        print("\n\n\nНагрузочный тест завершён\n\n\n")

    def results_processing(self):
        print("\n\n\nОбработка результатов\n\n\n")

        # "***Statistics for: DISCOVER-OFFER***" / "REQUEST-ACK***", внутри
        # каждой - чистые строки "label: value[ %|ms]". Если внутри секции
        # exchange не завершился, delay печатается как "inf ms"/"n/a" - такие
        # строки просто не попадают в числовой парсинг.
        kv_pattern = re.compile(r"^\s*([A-Za-z][\w \-/]*?)\s*:\s*(-?\d+(?:\.\d+)?)\s*(?:ms|%)?\s*$", re.MULTILINE)
        # Маркер шага содержит N - им режем общий файл на куски по шагам
        step_pattern = re.compile(re.escape(DHCP_PERFDHCP_STEP_MARKER_PREFIX) + r"(\d+)\s*===")

        def parse_step_sections(body: str):
            sections = {}

            def merge(section_name, section_body):
                values = {label.strip(): float(value) for label, value in kv_pattern.findall(section_body)}
                if values:
                    sections[section_name] = values

            parts = re.split(r"\*\*\*Statistics for:\s*(.+?)\*\*\*", body)
            merge("overall", parts[0])
            for i in range(1, len(parts), 2):
                merge(parts[i].strip(), parts[i + 1])
            return sections

        def load_kea_stat_values(file_path: str):
            with open(file_path) as f:
                data = json.load(f)
            arguments = data.get("arguments", {})
            return {name: samples[0][0] for name, samples in arguments.items() if samples}

        # pidstat -u -r в непрерывном режиме, остановлен SIGINT - в конце
        # печатает готовый "Average:" блок отдельно для CPU и для памяти
        def parse_pidstat_averages(text: str):
            lines = [ln for ln in text.splitlines() if ln.strip()]
            result = {}
            for i, line in enumerate(lines):
                if line.startswith("Average:") and "Command" in line and i + 1 < len(lines):
                    headers = line.split()
                    data_row = lines[i + 1].split()
                    if len(data_row) != len(headers) or data_row[0] != "Average:":
                        continue
                    row = dict(zip(headers, data_row))
                    if "%CPU" in row:
                        result["cpu_percent_avg"] = float(row["%CPU"])
                    if "RSS" in row:
                        result["rss_kb_avg"] = float(row["RSS"])
                    if "%MEM" in row:
                        result["mem_percent_avg"] = float(row["%MEM"])
            return result

        perfdhcp_by_step = {}
        perfdhcp_path = f"{BASE_PATH}/{DHCP_PERFDHCP_LOCAL_NAME}"
        if path.exists(perfdhcp_path):
            with open(perfdhcp_path) as f:
                perfdhcp_raw = f.read()

            chunks = step_pattern.split(perfdhcp_raw)
            for i in range(1, len(chunks), 2):
                n_target = int(chunks[i])
                body = chunks[i + 1]
                rate_achieved = re.findall(r"Rate:\s*(-?\d+(?:\.\d+)?)\s+.*?exchanges/second", body)
                rate_expected = re.findall(r"expected rate:\s*(-?\d+(?:\.\d+)?)", body)
                perfdhcp_by_step[n_target] = {
                    "sections": parse_step_sections(body),
                    "rate_achieved": float(rate_achieved[0]) if rate_achieved else None,
                    "rate_expected": float(rate_expected[0]) if rate_expected else None,
                }
        else:
            print(f'\n\nОшибка:\nФайл не найден: "{perfdhcp_path}"')

        steps = []
        for step_idx, n_target in enumerate(DHCP_PERFDHCP_CLIENT_STEPS):
            step_result = {"clients_target": n_target}

            step_perfdhcp = perfdhcp_by_step.get(n_target)
            if step_perfdhcp:
                step_result["perfdhcp"] = step_perfdhcp["sections"]
                step_result["perfdhcp_rate"] = {
                    "achieved": step_perfdhcp["rate_achieved"],
                    "expected": step_perfdhcp["rate_expected"] if step_perfdhcp["rate_expected"] is not None else DHCP_PERFDHCP_RATE,
                }
            else:
                print(f'\n\nОшибка:\nВ выводе perfdhcp не найден шаг N={n_target}\n')

            kea_before_path = f"{BASE_PATH}/kea_stats_before_step{step_idx}.json"
            kea_after_path = f"{BASE_PATH}/kea_stats_after_step{step_idx}.json"
            if path.exists(kea_before_path) and path.exists(kea_after_path):
                before_stats = load_kea_stat_values(kea_before_path)
                after_stats = load_kea_stat_values(kea_after_path)
                step_result["kea_stats_delta"] = {
                    name: after_stats[name] - before_stats.get(name, 0)
                    for name in after_stats
                }
            else:
                print(f'\n\nОшибка:\nФайлы статистики kea для шага {step_idx} (N={n_target}) не найдены\n')

            kea_proc_path = f"{BASE_PATH}/kea_proc_stats_step{step_idx}.txt"
            if path.exists(kea_proc_path):
                with open(kea_proc_path) as f:
                    step_result["kea_process"] = parse_pidstat_averages(f.read())
            else:
                print(f'\n\nОшибка:\nФайл pidstat для шага {step_idx} (N={n_target}) не найден\n')

            print(f"\nШаг N={n_target}:\n{step_result}")
            steps.append(step_result)

        dhcp_results = {"steps": steps}

        with open(DHCP_RESULTS, "w") as w:
            w.write(json.dumps(dhcp_results, ensure_ascii=False))

        print('\n\nЗабираем данные о ОС с ВМ\n')
        scp_vm_params = {
            DHCP_LOAD_CLIENT_VM: [
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
        print("\n\n\nОбработка результатов завершена\n\n\n")
