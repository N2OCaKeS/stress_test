from allta import Libvirt, LibvirtManager, SystemCommands

# Uncomment if use Total rating
# from allta import Criterion, MathModels

from pathlib import Path
from time import sleep

from net_conf import USERNAME, PASSWORD, VM_OS_INFO_PATH, BASE_PATH


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
            print("\n\n\nВМ успешно восстановлены продолжаем тест\n\n\n")

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
                        "xfs.1.7.5.o", self.rc_name, self.vms, VMS_DATES
                    )
                elif VERSION_OS == "1.8":
                    self.vms_data = self.provider.build(
                        "xfs.1.8.1.o",
                        self.rc_name,
                        self.vms,
                        VMS_DATES,
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
        print ("\n\n\nНачинаем выполнение теста\n\n\n")
        print ("\n\n\nВыполнение подготовки к тесту\n\n\n")
        # Подготовка к выполнению теста
        print("\n\n\nПодготовка завершена\n\n\n")



        params = ["off init_on_free", "on init_on_free"]
        for par in params:
            print(f"\n\n\nЗапускаем тест c {par}")
            if par == "on init_on_free":
                enable_init_on_free = {
                    "g_all": {
                        "enable init_on_free": {
                            "command": "f",
                            "signal set": "1",
                        },
                        "reboot": {
                            "command": "",
                            "signal get": "1",
                        },
                    }
                }
                self.provider.execute(
                    commands=enable_init_on_free,
                    vms_dates=self.vms_data,
                    vms_groups=self.vms_group,
                    username=USERNAME,
                    password=PASSWORD,
                )

            
            scp_get_result = {
                "testvm1": [
                    {
                        "mode": "pull",
                        "path_host": "/home/u", 
                        "path_vm": "/home/u/", # TODO Настроить получение результатов
                    },
                ]
            }
            self.provider.scp(
                scp_settings=scp_get_result,
                vms_dates=self.vms_data,
                vms_groups=self.vms_group,
                username=USERNAME,
                password=PASSWORD,
            )            

            print(f"\n\n\nТест с параметром {par} завершен")


        

    def results_processing(self):

        # Get vm params (av, kernel), params create in provision
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
        print ("results gets")
        pass
