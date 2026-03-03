from allta import Libvirt, LibvirtManager

from pathlib import Path
from time import sleep


from kernel_conf import (
    USERNAME,
    PASSWORD,
    VM_OS_INFO_PATH,
    BASE_PATH,
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
                    'command': """sudo sed -i 's/\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)"/\1 init_on_free=1 transparent_hugepage=never"/' /etc/default/grub""",
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
                    'command': 'mkfs.xfs -f xfs.file',
                    'signal get': 'dd xfsfile',
                    'signal set': 'mkfs xfs',
                },
                'mkdir': {
                    'command': 'mkdir xfs.mnt',
                    'signal get': 'mkfs xfs',
                    'signal set': 'mkdir xfs'
                },
                'mount': {
                    'command': 'mount -t xfs xfs.file xfs.mnt',
                    'signal get': 'mkdir xfs',
                    'signal set': 'mount xfs'
                },
            }
        }

        xfs_fill_out_file = {
            'testvm1': {
                'dd': {
                    'command': "dd if=/dev/zero bs=4096 count=100 | tr '\0' '\1' > xfs.mnt/test_file",
                    'signal set': 'xfs fillout'
                }
            }
        }

        scp_test_files = {
            "testvm1": [
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/fill', 
                    "path_vm": ..., 
                },
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/test1', 
                    "path_vm": ..., 
                },
            ]
        }

        start_test1 = {
            'testvm1': {
                'test1': {
                    'command': './test1 > test1_output.txt',
                    'signal set': 'test1 start',
                    'nowait': True,
                    'nowait_mode': 'terminate',
                    'nowait_timeout': 180
                }
            }
        }

        start_fill = {
            'testvm1': {
                'fill': {
                    'command': './fill >> fill_output.txt',
                    'signal set': 'fill start',
                    'nowait': True,
                    'nowait_mode': 'terminate',
                    'nowait_timeout': 180
                }
            }
        }

        print('Включение опции init_on_free')
        self.provider.execute(commands=init_on_free_on, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        print('Подготовка xfs')
        self.provider.execute(commands=xfs_create, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        self.provider.execute(commands=xfs_fill_out_file, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        print("Перенос тестовых файлов")
        self.provider.scp(scp_settings=scp_test_files, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)         
        print("\n\n\nПодготовка завершена\n\n\n")



        print("\n\n\nЗапускаем тест\n\n\n")
        
        print("\n\n\nТест завершен\n\n\n")

    def results_processing(self):

        print("\n\n\nОбработка результатов\n\n\n")
        # Обработка результатов
        print("\n\n\n Результаты обработаны\n\n\n")

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
        pass
