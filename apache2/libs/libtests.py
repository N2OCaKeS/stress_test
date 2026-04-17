

from time import sleep
from pathlib import Path

from allta import Libvirt, LibvirtManager, SystemCommands
from apa_conf import (
    SCRIPT_DIR, 
    USERNAME, 
    PASSWORD, 
    TESTED_QA_USER,
    TESTED_QA_USER_MAC,
    TESTED_QA_USER_MAC_CAT,
    CONCURRENCY,
    CONCURRENCY_STEP,
    MAX_CONCURRENCY,
    MAX_REQUESTS,
    CSV_RESULTS_FILE,
    PLOT_FILE,
    AB_OUTPUT_FILE_PAM,
    AB_OUTPUT_FILE_NOPAM
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

        self.vms_date_save_path = f"{SCRIPT_DIR}/vms_dates.json"
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
                    {
                        "mode": "push",
                        "path_host": f"{self.testdir}/provision/ab-graph.sh",
                        "path_vm": "/home/u/ab-graph.sh",
                    },
                    {
                        "mode": "push",
                        "path_host": f"{self.testdir}/provision/ab-plot.sh",
                        "path_vm": "/home/u/ab-plot.sh",
                    },
                    {
                        "mode": "push",
                        "path_host": f"{self.testdir}/provision/apache_prepare.sh",
                        "path_vm": "/home/u/apache_prepare.sh",
                    },
                    {
                        "mode": "push",
                        "path_host": f"{self.testdir}/provision/apache_server_prepare.sh",
                        "path_vm": "/home/u/apache_server_prepare.sh",
                    },
                    {
                        "mode": "push",
                        "path_host": f"{self.testdir}/provision/libald.sh",
                        "path_vm": "/home/u/libald.sh",
                    },
                    {
                        "mode": "push",
                        "path_host": f"{self.testdir}/provision/liblocal.sh",
                        "path_vm": "/home/u/liblocal.sh",
                    },
                    {
                        "mode": "push",
                        "path_host": f"{self.testdir}/provision/000-default-no-pam.conf",
                        "path_vm": "/home/u/000-default-no-pam.conf",
                    },
                    {
                        "mode": "push",
                        "path_host": f"{self.testdir}/provision/000-default-pam.conf",
                        "path_vm": "/home/u/000-default-pam.conf",
                    },
                    {
                        "mode": "push",
                        "path_host": f"{self.testdir}/provision/apache2",
                        "path_vm": "/home/u/apache2",
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



class ApacheBenchPam(CreateVM):
    def create_test_env(self):
        """
        testvm1 - server
        testvm2 - client
        """

        print ("\n\n\nНачинаем подготовку тестового окружения\n\n\n")
        print ("\n\n\nПодготовка клиента\n\n\n")
        client_prepare = {
            "testvm2": {
                "add_category": {
                    "command": f"sudo usercat -a 8 'Cat_8'",
                    "signal set": "add_category",
                },
                "add_user": {
                    "command": f"sudo yes '1' | sudo adduser {TESTED_QA_USER}",
                    "signal set": "add_user",
                    "signal get": "add_category",
                },
                "add_user": {
                    "command": f"sudo yes '1' | sudo adduser {TESTED_QA_USER_MAC}",
                    "signal set": "add_user_mac",
                    "signal get": "add_user",
                },
                "add_user": {
                    "command": f"sudo yes '1' | sudo adduser {TESTED_QA_USER_MAC_CAT}",
                    "signal set": "add_user_mac_cat",
                    "signal get": "add_user_mac",
                },
                "set_level": {
                    "command": f"sudo pdpl-user -i 0 -l 2:2 -c 0x0:0x0 {TESTED_QA_USER_MAC}",
                    "signal set": "set_level",
                    "signal get": "add_user_mac_cat",
                },
                "set_level": {
                    "command": f"sudo pdpl-user -i 0 -l 2:2 -c 0xA:0xA {TESTED_QA_USER_MAC_CAT}",
                    "signal set": "set_level_cat",
                    "signal get": "set_level",
                },
            }
        }

        self.provider.execute(commands=client_prepare, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        print ("\n\n\nПодготовка клиента завершена\n\n\n")

        print ("\n\n\nПодготовка сервера\n\n\n")
        server_prepare = {
            "testvm1": {
                "server_prepare": {
                    "command": f"cd /home/u && sudo bash apache_server_prepare.sh pam",
                    "signal set": "server_prepare",
                },
                "copy": {
                    "command": f"sudo cp /home/u/apache2 /etc/pam.d/apache2",
                    "signal set": "copy_apache",
                    "signal get": "server_prepare",
                },
                "pam_tally": {
                    "command": f"echo 'account required pam_tally.so' | sudo tee -a /etc/pam.d/apache2",
                    "signal set": "pam_tally",
                    "signal get": "copy_apache",
                },
                "copy_sa": {
                    "command": f"sudo cp /home/u/000-default-pam.conf /etc/apache2/sites-available/000-default.conf",
                    "signal set": "copy_sa",
                    "signal get": "pam_tally",
                },
                "usermod": {
                    "command": f"sudo usermod -aG shadow www-data",
                    "signal set": "usermod",
                    "signal get": "copy_sa",
                },
                "server_prepare_end": {
                    "command": f"cd /home/u && sudo bash apache_server_prepare.sh pam",
                    "signal set": "server_prepare_end",
                    "signal get": "usermod",
                },
                "add_user": {
                    "command": f"sudo yes '1' | sudo adduser {TESTED_QA_USER}",
                    "signal set": "add_user",
                    "signal get": "server_prepare_end",
                },
                "add_user": {
                    "command": f"sudo yes '1' | sudo adduser {TESTED_QA_USER_MAC}",
                    "signal set": "add_user_mac",
                    "signal get": "add_user",
                },
                "add_user": {
                    "command": f"sudo yes '1' | sudo adduser {TESTED_QA_USER_MAC_CAT}",
                    "signal set": "add_user_mac_cat",
                    "signal get": "add_user_mac",
                },
                "set_level": {
                    "command": f"sudo pdpl-user -i 0 -l 2:2 -c 0x0:0x0 {TESTED_QA_USER_MAC}",
                    "signal set": "set_level",
                    "signal get": "add_user_mac_cat",
                },
                "set_level": {
                    "command": f"sudo pdpl-user -i 0 -l 2:2 -c 0xA:0xA {TESTED_QA_USER_MAC_CAT}",
                    "signal set": "set_level_cat",
                    "signal get": "set_level",
                },
            }
        }

        self.provider.execute(commands=server_prepare, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        print ("\n\n\nПодготовка сервера завершена\n\n\n")


    def start_test(self):
        """
        testvm1 - server
        testvm2 - client
        """
        
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

        print ("\n\n\nНачинаем выполнение теста\n\n\n")
        astra_mode_switch_enable = {
            "testvm1": {
                "astra_mode": {
                    "command": f"sudo sed -i -e 's/# AstraMode on/AstraMode on/' /etc/apache2/apache2.conf",
                    "signal set": "astra_mode_1",
                },
                "astra_mode_2": {
                    "command": f"sudo sed -i -e 's/AstraMode off/AstraMode on/' /etc/apache2/apache2.conf",
                    "signal set": "astra_mode_2",
                    "signal get": "astra_mode_1",
                },
                "restart_service": {
                    "command": f"sudo systemctl restart apache2.service",
                    "signal set": "restart",
                    "signal get": "astra_mode_2",
                },
            }
        }
        astra_mode_switch_disable = {
            "testvm1": {
                "astra_mode": {
                    "command": f"sudo sed -i -e 's/# AstraMode on/AstraMode off/' /etc/apache2/apache2.conf",
                    "signal set": "astra_mode_1",
                },
                "astra_mode_2": {
                    "command": f"sudo sed -i -e 's/AstraMode on/AstraMode off/' /etc/apache2/apache2.conf",
                    "signal set": "astra_mode_2",
                    "signal get": "astra_mode_1",
                },
                "restart_service": {
                    "command": f"sudo systemctl restart apache2.service",
                    "signal set": "restart",
                    "signal get": "astra_mode_2",
                },
            }
        }

        self.provider.execute(commands=astra_mode_switch_enable, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        server_ip = self.vms_data['testvm1']['ip_bridge']
        cat_urls = {
            f'http://{server_ip}/lev2.html': TESTED_QA_USER_MAC,
            f'http://{server_ip}/lev2catA.html': TESTED_QA_USER_MAC_CAT
        }
        nocat_urls = {
            f'http://{server_ip}/lev0.html': TESTED_QA_USER
        }

        print ("\n\n\nНачинаем выполнение теста Apache pam\n\n\n")
        for url, user in cat_urls.values():
            for concurrent in range(CONCURRENCY_STEP, MAX_CONCURRENCY, CONCURRENCY_STEP):
                abp_test_command = f"""
                    /usr/bin/ab -c {concurrent} -n {MAX_REQUESTS} -e {CSV_RESULTS_FILE} -g {PLOT_FILE} -A {user}:1 {url} >> {AB_OUTPUT_FILE_PAM}
                """
                abp_test = {
                    "testvm2": {
                        "run_test": {
                            "command": f"{abp_test_command}",
                            "signal set": "run_test",
                        },
                    }
                }

                self.provider.execute(commands=abp_test, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        print ("\n\n\nApache pam завершен\n\n\n")

        print ("\n\n\nНачинаем выполнение теста Apache no_pam\n\n\n")
        apache_pam_reset = {
            "testvm1": {
                "copy": {
                    "command": f"sudo cp /home/u/apache2 /etc/pam.d/apache2",
                    "signal set": "copy_apache",
                },
                "000-default": {
                    "command": f"sudo cp /home/u/000-default-no-pam.conf /etc/apache2/sites-available/000-default.conf",
                    "signal set": "000-default",
                    "signal get": "copy_apache",
                },
                "server_prepare": {
                    "command": f"cd /home/u && sudo bash apache_server_prepare.sh pam",
                    "signal set": "server_prepare",
                    "signal get": "000-default",
                },
            }    
        }
        self.provider.execute(commands=apache_pam_reset, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        self.provider.execute(commands=astra_mode_switch_disable, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        for url, user in nocat_urls.values():
            for concurrent in range(CONCURRENCY_STEP, MAX_CONCURRENCY, CONCURRENCY_STEP):
                abp_test_command_nopam = f"""
                    /usr/bin/ab -c {concurrent} -n {MAX_REQUESTS} -e {CSV_RESULTS_FILE} -g {PLOT_FILE} {url} >> {AB_OUTPUT_FILE_NOPAM}
                """
                abp_test_nopam = {
                    "testvm2": {
                        "run_test": {
                            "command": f"{abp_test_command_nopam}",
                            "signal set": "run_test",
                        },
                    }
                }

                self.provider.execute(commands=abp_test_nopam, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        print ("\n\n\nApache no_pam завершен\n\n\n")

       