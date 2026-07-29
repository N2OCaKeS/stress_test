from allta import Libvirt, LibvirtManager, SystemCommands
from time import sleep
from pathlib import Path
from aeb_conf import BASE_PATH, PASSWORD, USERNAME, VM_OS_INFO_PATH, VM_RESULTS_PATH

# Путь на ВМ, куда astraeventsd_load_test.py складывает сырые json-результаты
REMOTE_RESULTS_DIR = "/home/u/results"

LOAD_TEST_EVENTS = "server_started,server_stopped,printer_added,job_created"

class CreateVM:
    def __init__(
        self,
        rc_name: str = "",
        testdir: str = "",
        kernel: str = SystemCommands.check_output_command("uname -r"),
        vm_count: int = 0,
        vcpu_min: int = 0,
        ram_min: int = 0,
        vcpu_max: int = 0,
        ram_max: int = 0,
    ):

        self.provider = Libvirt()

        self.rc_name = rc_name
        self.kernel = kernel

        self.vm_count = vm_count
        self.vcpu_min = vcpu_min
        self.ram_min = ram_min
        self.vcpu_max = vcpu_max
        self.ram_max = ram_max

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
            vm_resources = {
                "testvm1": {"cpu": self.vcpu_min, "ram": self.ram_min},
                "testvm2": {"cpu": self.vcpu_max, "ram": self.ram_max},
            }
            VMS_DATES = {
                testvm: {
                    "host-port": "22",
                    "cpu": str(vm_resources[testvm]["cpu"]),
                    "ram": str(vm_resources[testvm]["ram"]),
                }
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


class AstraEventsLoadTest(CreateVM):
    def start_test(self):
        """
        testvm1 - ВМ с меньшим количеством ресурсов, на которой будет выполняться нагрузка
        testvm2 - ВМ с большим количеством ресурсов, на которой будет выполняться нагрузка
        """

        scp_test_files = {
            "testvm1": [
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/astraeventsd_load_test.py', 
                    "path_vm": '/home/u/astraeventsd_load_test.py', 
                },
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/event-generator', 
                    "path_vm": '/home/u/event-generator', 
                },
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/event_generator.yaml', 
                    "path_vm": '/home/u/event_generator.yaml', 
                },
                
            ],
            "testvm2": [
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/astraeventsd_load_test.py', 
                    "path_vm": '/home/u/astraeventsd_load_test.py', 
                },
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/event-generator', 
                    "path_vm": '/home/u/event-generator', 
                },
                {
                    "mode": "push",
                    "path_host": f'{BASE_PATH}/event_generator.yaml', 
                    "path_vm": '/home/u/event_generator.yaml', 
                },  
            ]
        }
        print("Перенос тестовых файлов")
        self.provider.scp(scp_settings=scp_test_files, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        print("\n\n\nПодготовка завершена\n\n\n")

        print("\n\n\nЗапускаем тест по очереди на каждой ВМ\n\n\n")
        for vm in self.vms:
            start_test = {
                vm: {
                    "chmod_generator": {
                        "command": "sudo chmod +x /home/u/event-generator",
                        "signal set": "generator_ready",
                    },
                    "run_test": {
                        "command": (
                            "sudo python3 /home/u/astraeventsd_load_test.py "
                            "--generator-bin /home/u/event-generator "
                            f"--events {LOAD_TEST_EVENTS} "
                            f"--out-dir {REMOTE_RESULTS_DIR}"
                        ),
                        "signal get": "generator_ready",
                    },
                }
            }
            print(f"\n\n\nЗапускаем тест на {vm}\n\n\n")
            self.provider.execute(
                commands=start_test,
                vms_dates=self.vms_data,
                vms_groups=self.vms_group,
                username=USERNAME,
                password=PASSWORD,
            )
            print(f"\n\n\nТест на {vm} завершён\n\n\n")
        print("\n\n\nТесты на всех ВМ завершены\n\n\n")


    def results_processing(self):

        sleep(120)
        print("\n\n\nЗабираем данные о ОС с ВМ\n\n\n")
        scp_vm_params = {}
        for vm in self.vms:
            Path(f"{VM_OS_INFO_PATH}/{vm}").mkdir(parents=True, exist_ok=True)
            scp_vm_params[vm] = [
                {
                    "mode": "pull",
                    "path_host": f"{VM_OS_INFO_PATH}/{vm}",
                    "path_vm": "/home/u/av.txt",
                },
                {
                    "mode": "pull",
                    "path_host": f"{VM_OS_INFO_PATH}/{vm}",
                    "path_vm": "/home/u/kernel.txt",
                },
            ]
        self.provider.scp(
            scp_settings=scp_vm_params,
            vms_dates=self.vms_data,
            vms_groups=self.vms_group,
            username=USERNAME,
            password=PASSWORD,
        )
        print("\n\n\nДанные о ОС с ВМ собраны\n\n\n")

        print("\n\n\nЗабираем сырые json-результаты нагрузочного теста с ВМ\n\n\n")
        scp_test_results = {}
        for vm in self.vms:
            Path(f"{VM_RESULTS_PATH}/{vm}").mkdir(parents=True, exist_ok=True)
            scp_test_results[vm] = [
                {
                    "mode": "pull",
                    "path_host": f"{VM_RESULTS_PATH}/{vm}",
                    "path_vm": f"{REMOTE_RESULTS_DIR}/report.json",
                }
            ]
        self.provider.scp(
            scp_settings=scp_test_results,
            vms_dates=self.vms_data,
            vms_groups=self.vms_group,
            username=USERNAME,
            password=PASSWORD,
        )
        print("\n\n\nРезультаты нагрузочного теста собраны\n\n\n")

        print("\n\n\nОбработка результатов\n\n\n")

        """
        Обработка результатов теста
        """

    
    