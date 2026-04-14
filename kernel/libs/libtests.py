import json

from time import sleep
from pathlib import Path

from allta import Libvirt, LibvirtManager, SystemCommands

from kernel_conf import (
    USERNAME,
    PASSWORD,
    VM_OS_INFO_PATH,
    BASE_PATH,
    VM_TEST1_OUTPUT,
    VM_TEST2_OUTPUT,
    RESULTS_FILE
    # IOF_OFF_PATH,
    # IOF_ON_PATH,
    # IOF_ON_NAME,
    # IOF_OFF_NAME,
    # ITERATIONS,
    # IOF_RESULTS,
)


class CreateVM:
    def __init__(
        self,
        rc_name: str = "",
        testdir: str = "",
        kernel: str = SystemCommands.check_output_command("uname -r"),
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
            print(
                "\n\n\nВМ существуют, восстанавливаем в состояние выполненного provison\n\n\n"
            )
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
                        box="1.7.5.o",
                        rc=self.rc_name,
                        vms=self.vms,
                        vms_dates=VMS_DATES,
                        kernel=self.kernel,
                    )
                elif VERSION_OS == "1.8":
                    self.vms_data = self.provider.build(
                        box="1.8.1.o",
                        rc=self.rc_name,
                        vms=self.vms,
                        vms_dates=VMS_DATES,
                        kernel=self.kernel,
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

            print("\n\n\nДелаем снимок для быстрого дебага\n\n\n")
            LibvirtManager.Vm.stop(self.vms)
            LibvirtManager.Snapshot.create(vms=self.vms, snapshot_name="provision")
            LibvirtManager.Vm.start(self.vms)
            sleep(90)
            self.provider.check(vms=self.vms, vms_dates=self.vms_data)
            print("\n\n\nСнимок создан, ВМ созданы и к выполнению теста готовы\n\n\n")

    def vms_destroy(self):
        print("\n\n\nВыключаем ВМ:\n\n\n")
        LibvirtManager.Vm.stop(vms=self.vms)


class Sigmentation_fault(CreateVM):
    def start_test(self):
        """
        testvm1 - ВМ для теста
        """

        print("\n\n\nНачинаем выполнение теста\n\n\n")
        print("\n\n\nВыполнение подготовки к тесту\n\n\n")
        """
            1) dd if=/dev/zero of=xfs.file bs=1M count=384
            2) mkfs.xfs -f xfs.file
            3) mkdir xfs.mnt
            4) mount -t xfs xfs.file xfs.mnt
            5) fill.c ... gcc -o fill fill.c
        """
        # Подготовка к выполнению теста
        init_on_free_on = {
            'testvm1': {
                'init_on_free_off': {
                    'command': r"""sudo sed -i 's/\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)"/\1 init_on_free=1 transparent_hugepage=never"/' /etc/default/grub""",
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

        xfs_create = {
            'testvm1': {
                'dd': {
                    'command': 'dd if=/dev/zero of=xfs.file bs=1M count=384',
                    'signal set': 'dd xfsfile'
                },
                'mkfs_xfs': {
                    'command': 'sudo mkfs.xfs -f xfs.file',
                    'signal get': 'dd xfsfile',
                    'signal set': 'mkfs xfs',
                },
                'mkdir': {
                    'command': 'mkdir xfs.mnt',
                    'signal get': 'mkfs xfs',
                    'signal set': 'mkdir xfs'
                },
                'mount': {
                    'command': 'sudo mount -t xfs xfs.file xfs.mnt',
                    'signal get': 'mkdir xfs',
                    'signal set': 'mount xfs'
                },
                'dd_test_file': {
                    'command': "dd if=/dev/zero bs=4096 count=100 | tr '\\0' '\\1' | sudo tee /home/u/xfs.mnt/test_file",
                    'signal get': 'mount xfs',
                    'signal set': 'dd testfile'
                }
            }
        }

        # xfs_fill_out_file = {
        #     'testvm1': {
        #         'dd_test_file': {
        #             'command': "sudo bash -c \"dd if=/dev/zero bs=4096 count=100 | tr '\0' '\1' > /home/u/xfs.mnt/test_file\"",
        #             'signal set': 'dd testfile'
        #         }
        #     }
        # }

        scp_test_files = {
            "testvm1": [
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/fill.c', 
                    "path_vm": '/home/u/fill.c', 
                },
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/test1.c', 
                    "path_vm": '/home/u/test1.c', 
                },
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/test2.c',
                    "path_vm": '/home/u/test2.c'
                },
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/fill.py',
                    "path_vm": '/home/u/fill.py'
                },
                # {
                #     "mode": "push",
                #     "path_host": f'{BASE_PATH}/test1.py',
                #     "path_vm": '/home/u/test1.py'
                # },
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/test2.py',
                    "path_vm": '/home/u/test2.py'
                },
            ]
        }

        start_test1_and_fill = {
            'testvm1': {
                'compile_test1': {
                    'command': 'gcc -o /home/u/test1 /home/u/test1.c',
                    'signal set': 'compile test1'
                },
                'test1': {
                    'command': '/home/u/test1 > /home/u/test1_output.txt',
                    'signal get': 'compile test1',
                    'signal set': 'test1 start',
                    'nowait': True,
                    'nowait_mode': 'terminate',
                    'nowait_timeout': 190
                },
                'fill': {
                    'command': 'python3 fill.py',
                    'signal get': 'test1 start',
                    'signal set': 'fill start',
                    'nowait': True,
                    'nowait_mode': 'terminate',
                    'nowait_timeout': 180
                }
            }
        }

        start_test2_and_fill = {
            'testvm1': {
                'test1': {
                    'command': 'sudo python3 test2.py',
                    'signal set': 'test2 start',
                    'nowait': True,
                    'nowait_mode': 'terminate',
                    'nowait_timeout': 310
                },
                'fill': {
                    'command': 'sudo python3 fill.py',
                    'signal get': 'test2 start',
                    'signal set': 'fill start',
                    'nowait': True,
                    'nowait_mode': 'terminate',
                    'nowait_timeout': 300
                }
            }
        }

        chown_test2_output = {
            'testvm1': {
                'chown': {
                    'command': 'sudo chown u:u /home/u/test2_output.txt',
                    'signal set': 'chown test2 output',
                }
            }
        }


        print('Включение опции init_on_free')
        self.provider.execute(commands=init_on_free_on, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        print('Подготовка xfs')
        self.provider.execute(commands=xfs_create, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        # self.provider.execute(commands=xfs_fill_out_file, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)


        print("Перенос тестовых файлов")
        self.provider.scp(scp_settings=scp_test_files, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)         
        print("\n\n\nПодготовка завершена\n\n\n")
        print("\n\n\nЗапускаем тест\n\n\n")

        self.provider.execute(commands=start_test1_and_fill, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        self.provider.execute(commands=start_test2_and_fill, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        self.provider.execute(commands=chown_test2_output, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        print("\n\n\nТест завершен\n\n\n")

    def results_processing(self):

        sleep(120)
        print("\n\n\nЗабираем данные о ОС с ВМ\n\n\n")
        scp_vm_params = {
            "testvm1": [
                {
                    "mode": "pull",
                    "path_host": VM_OS_INFO_PATH,
                    "path_vm": "/home/u/av.txt",
                },
                {
                    "mode": "pull",
                    "path_host": VM_OS_INFO_PATH,
                    "path_vm": "/home/u/kernel.txt",
                },
                {
                    "mode": "pull",
                    "path_host": VM_TEST1_OUTPUT,
                    "path_vm": "/home/u/test1_output.txt"
                },
                {
                    "mode": "pull",
                    "path_host": VM_TEST2_OUTPUT,
                    "path_vm": "/home/u/test2_output.txt"
                }
            ]
        }
        self.provider.scp(
            scp_settings=scp_vm_params,
            vms_dates=self.vms_data,
            vms_groups=self.vms_group,
            username=USERNAME,
            password=PASSWORD,
        )
        print("\n\n\nДанные о ОС с ВМ собраны\n\n\n")
        print("\n\n\nОбработка результатов\n\n\n")
        # Обработка результатов
        
        status_test1_bug = False
        with open(VM_TEST1_OUTPUT, 'r') as test1_file:
            test1_output = test1_file.readlines()
            for line in test1_output:
                if "Got invalid value on page" in line:
                    status_test1_bug = True
                    break

        status_test2_bug = False
        with open(VM_TEST2_OUTPUT, 'r') as test2_file:
            test2_output = test2_file.readlines()
            for line in test2_output:
                if "Ошибка сегментирования" in line:
                    status_test2_bug = True
                    break
        
        result = {
            'status_test1': status_test1_bug,
            'status_test2': status_test2_bug
        }
        with open(RESULTS_FILE, 'w') as result_file:
            result_file.write(json.dumps(result))

        print("\n\n\n Результаты обработаны\n\n\n")
            

class XFSMemoryLeak(CreateVM):
    def start_test(self):
        scp_test_files = {
            "testvm1": [
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/provision/copy_files.sh', 
                    "path_vm": '/home/u/copy_files.sh', 
                },
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/get_info.py', 
                    "path_vm": '/home/u/get_info.py', 
                },
            ]
        }

        start_test = {
            'testvm1': {
                'copy_files': {
                    'command': 'sudo python3 start_xfs_test.py',
                    'signal set': 'start test',
                }
            }
        }

        print("Перенос тестовых файлов")
        self.provider.scp(scp_settings=scp_test_files, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        print("\n\n\nПодготовка завершена\n\n\n")
        print("\n\n\nЗапускаем тест\n\n\n")

        self.provider.execute(commands=start_test, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)


    def results_processing(self):
        sleep(120)
        print("\n\n\nЗабираем данные о ОС с ВМ\n\n\n")
        scp_vm_params = {
            "testvm1": [
                {
                    "mode": "pull",
                    "path_host": VM_OS_INFO_PATH,
                    "path_vm": "/home/u/av.txt",
                },
                {
                    "mode": "pull",
                    "path_host": VM_OS_INFO_PATH,
                    "path_vm": "/home/u/kernel.txt",
                },
                {
                    "mode": "pull",
                    "path_host": f"{BASE_PATH}/copy_output.txt",
                    "path_vm": "/home/u/copy_output.txt"
                },
                {
                    "mode": "pull",
                    "path_host": f"{BASE_PATH}/ram_usage_log.txt",
                    "path_vm": "/home/u/ram_usage_log.txt"
                },
                {
                    "mode": "pull",
                    "path_host": f"{BASE_PATH}/results.json",
                    "path_vm": "/home/u/results.json"
                }

            ]
        }
        self.provider.scp(
            scp_settings=scp_vm_params,
            vms_dates=self.vms_data,
            vms_groups=self.vms_group,
            username=USERNAME,
            password=PASSWORD,
        )
        print("\n\n\nДанные о ОС с ВМ собраны\n\n\n")
        print("\n\n\nОбработка результатов\n\n\n")
        
        
