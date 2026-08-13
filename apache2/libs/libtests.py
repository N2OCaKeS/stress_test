import os
import re

from time import sleep
from pathlib import Path

from allta import Libvirt, LibvirtManager, SystemCommands, MathModel

from apa_parse_results import build_tables_separately_for_each_lvl_or_category
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
    AB_OUTPUT_FILE_NOPAM,
    AB_OUTPUT_FILE_BALANCE,
    A_BALANCE_KEEPALIVED_AUTH_PASS,
    A_BALANCE_KEEPALIVED_CHECK_INTERVAL,
    A_BALANCE_KEEPALIVED_LB1_PRIORITY,
    A_BALANCE_KEEPALIVED_LB2_PRIORITY,
    A_BALANCE_KEEPALIVED_VRID,
    A_BALANCE_VIP,
    REPORT_PATH,
    VM_OS_INFO_PATH,
    VM_KERNEL,
    VM_INFONAME,
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
                "report": {
                    "command": f"sudo mkdir -p {REPORT_PATH} && sudo chmod -R 777 {REPORT_PATH}",
                    "signal set": "add_report",
                },
                "add_category": {
                    "command": f"sudo usercat -a 8 'Cat_8'",
                    "signal set": "add_category",
                    "signal get": "add_report",
                },
                "add_user": {
                    "command": f"sudo yes '1' | sudo adduser {TESTED_QA_USER}",
                    "signal set": "add_user",
                    "signal get": "add_category",
                },
                "add_user2": {
                    "command": f"sudo yes '1' | sudo adduser {TESTED_QA_USER_MAC}",
                    "signal set": "add_user_mac",
                    "signal get": "add_user",
                },
                "add_user3": {
                    "command": f"sudo yes '1' | sudo adduser {TESTED_QA_USER_MAC_CAT}",
                    "signal set": "add_user_mac_cat",
                    "signal get": "add_user_mac",
                },
                "set_level": {
                    "command": f"sudo pdpl-user -i 0 -l 2:2 -c 0x0:0x0 {TESTED_QA_USER_MAC}",
                    "signal set": "set_level",
                    "signal get": "add_user_mac_cat",
                },
                "set_level2": {
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
                "report": {
                    "command": f"sudo mkdir -p {REPORT_PATH} && sudo chmod -R 777 {REPORT_PATH}",
                    "signal set": "add_report",
                },
                "server_prepare": {
                    "command": f"cd /home/u && sudo bash apache_server_prepare.sh pam",
                    "signal set": "server_prepare",
                    "signal get": "add_report",
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
                "add_user2": {
                    "command": f"sudo yes '1' | sudo adduser {TESTED_QA_USER_MAC}",
                    "signal set": "add_user_mac",
                    "signal get": "add_user",
                },
                "add_user3": {
                    "command": f"sudo yes '1' | sudo adduser {TESTED_QA_USER_MAC_CAT}",
                    "signal set": "add_user_mac_cat",
                    "signal get": "add_user_mac",
                },
                "set_level": {
                    "command": f"sudo pdpl-user -i 0 -l 2:2 -c 0x0:0x0 {TESTED_QA_USER_MAC}",
                    "signal set": "set_level",
                    "signal get": "add_user_mac_cat",
                },
                "set_level2": {
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
        for url, user in cat_urls.items():
            for concurrent in [1] + list(range(CONCURRENCY_STEP, MAX_CONCURRENCY, CONCURRENCY_STEP)):
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

        for url, user in nocat_urls.items():
            for concurrent in [1] + list(range(CONCURRENCY_STEP, MAX_CONCURRENCY, CONCURRENCY_STEP)):
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


        scp_results = {
            "testvm2": [
                {
                    "mode": "pull",
                    "path_host": f"{self.testdir}/summary_no-pam.txt",
                    "path_vm": f"{AB_OUTPUT_FILE_NOPAM}",
                },
                {
                    "mode": "pull",
                    "path_host": f"{self.testdir}/summary_pam.txt",
                    "path_vm": f"{AB_OUTPUT_FILE_PAM}",
                },
            ]
        }
        self.provider.scp(scp_settings=scp_results, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        if os.path.isfile(f"{self.testdir}/summary_pam.txt") and os.path.isfile(f"{self.testdir}/summary_no-pam.txt"):
            print (f"\n\n\nРезультаты успешно скопированы на сервер и расположены в {self.testdir}\n\n\n")
        else:
            print ("\n\nFail\nНе удалось скопировать результаты теста с ВМ\n\n\n")


    def preprocessing_results(self):

        sleep(120)
        print("\n\n\nЗабираем данные о ОС с ВМ\n\n\n")
        scp_vm_params = {
            "testvm1": [
                {
                    "mode": "pull",
                    "path_host": VM_INFONAME,
                    "path_vm": "/home/u/av.txt",
                },
                {
                    "mode": "pull",
                    "path_host": VM_KERNEL,
                    "path_vm": "/home/u/kernel.txt",
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

        model = MathModel()
        for lvl in build_tables_separately_for_each_lvl_or_category():   
            model.add_criterion(
                f"{lvl}_rps",
                iterations=lvl.index.tolist(),
                values=lvl["requests_per_second"].tolist(),
                weight=0.16666,
                negative=False,
                bounds=(0.0, 140000.0),
            )
            model.add_criterion(
                f"{lvl}_waiting",
                iterations=lvl.index.tolist(),
                values=lvl["waiting_median_ms"].tolist(),
                weight=0.16666,
                negative=True,
                bounds=(0.0, 65000.0),
            )

        fixed_power = 0.9996180247850317
        result = model.total_rating(power=fixed_power)  

        return round(result['total_rating'] / 100)


class ApacheBalance(CreateVM):
    def create_test_env(self):
        """
        testvm1 - Apache load balancer + keepalived MASTER
        testvm2 - Apache load balancer + keepalived BACKUP
        testvm3 - first backend Apache
        testvm4 - second backend Apache
        testvm5 - ApacheBench client
        """

        print ("\n\n\nНачинаем подготовку тестового окружения\n\n\n")

        backend_1_ip = self.vms_data["testvm3"]["ip_bridge"]
        backend_2_ip = self.vms_data["testvm4"]["ip_bridge"]
        lb_master_ip = self.vms_data["testvm1"]["ip_bridge"]
        lb_backup_ip = self.vms_data["testvm2"]["ip_bridge"]
        vip = A_BALANCE_VIP

        balancer_conf = f"""<VirtualHost *:80>
ServerName apache-balance
ProxyPreserveHost On
ProxyRequests Off
ProxyPass / balancer://apache_balance_cluster/
ProxyPassReverse / balancer://apache_balance_cluster/
AstraMode off
ProxyTimeout 10
<Proxy balancer://apache_balance_cluster>
    BalancerMember http://{backend_1_ip}:80
    BalancerMember http://{backend_2_ip}:80
    ProxySet lbmethod=byrequests
</Proxy>
ErrorLog ${{APACHE_LOG_DIR}}/balance_error.log
CustomLog ${{APACHE_LOG_DIR}}/balance_access.log combined
</VirtualHost>"""
        remote_backend_conf = """<VirtualHost *:80>
DocumentRoot /var/www
ErrorLog ${APACHE_LOG_DIR}/backend_error.log
CustomLog ${APACHE_LOG_DIR}/backend_access.log combined
</VirtualHost>"""
        backend_html_prepare_command = (
            "sudo mkdir -p /var/www; "
            "printf '<html><body><h2>Apache balance backend: $(hostname)</h2></body></html>' | sudo tee /var/www/index.html >/dev/null; "
            "sudo chmod 644 /var/www/index.html"
        )
        keepalived_base_command = (
            "command -v keepalived >/dev/null || "
            "(sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y keepalived); "
            "sudo sysctl -w net.ipv4.ip_nonlocal_bind=1; "
        )
        keepalived_master_conf = f"""vrrp_script chk_apache {{
    script "/usr/bin/systemctl is-active --quiet apache2"
    interval {A_BALANCE_KEEPALIVED_CHECK_INTERVAL}
    fall 2
    rise 2
}}

vrrp_instance apache_balance_vip {{
    state MASTER
    interface __INTERFACE__
    virtual_router_id {A_BALANCE_KEEPALIVED_VRID}
    priority {A_BALANCE_KEEPALIVED_LB1_PRIORITY}
    advert_int 1
    authentication {{
        auth_type PASS
        auth_pass {A_BALANCE_KEEPALIVED_AUTH_PASS}
    }}
    unicast_src_ip {lb_master_ip}
    unicast_peer {{
        {lb_backup_ip}
    }}
    virtual_ipaddress {{
        {vip}/24
    }}
    track_script {{
        chk_apache
    }}
}}"""
        keepalived_backup_conf = f"""vrrp_script chk_apache {{
    script "/usr/bin/systemctl is-active --quiet apache2"
    interval {A_BALANCE_KEEPALIVED_CHECK_INTERVAL}
    fall 2
    rise 2
}}

vrrp_instance apache_balance_vip {{
    state BACKUP
    interface __INTERFACE__
    virtual_router_id {A_BALANCE_KEEPALIVED_VRID}
    priority {A_BALANCE_KEEPALIVED_LB2_PRIORITY}
    advert_int 1
    authentication {{
        auth_type PASS
        auth_pass {A_BALANCE_KEEPALIVED_AUTH_PASS}
    }}
    unicast_src_ip {lb_backup_ip}
    unicast_peer {{
        {lb_master_ip}
    }}
    virtual_ipaddress {{
        {vip}/24
    }}
    track_script {{
        chk_apache
    }}
}}"""

        print ("\n\n\nПодготовка клиента и backend-серверов\n\n\n")
        env_prepare = {
            "testvm5": {
                "report": {
                    "command": (
                        f"sudo mkdir -p {REPORT_PATH} && sudo chmod -R 777 {REPORT_PATH}; "
                        "command -v ab >/dev/null || "
                        "(sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y apache2-utils); "
                        "command -v curl >/dev/null || "
                        "(sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y curl)"
                    ),
                    "signal set": "add_report",
                },
                "clear_results": {
                    "command": f"sudo rm -f {AB_OUTPUT_FILE_BALANCE} {CSV_RESULTS_FILE} {PLOT_FILE}",
                    "signal set": "clear_results",
                    "signal get": "add_report",
                },
            },
            "testvm1": {
                "disable_astra_mode": {
                    "command": "sudo sed -i -e 's/# AstraMode on/AstraMode off/' -e 's/AstraMode on/AstraMode off/' /etc/apache2/apache2.conf",
                    "signal set": "disable_astra_mode",
                },
                "balancer_conf": {
                    "command": f"printf '%b' {balancer_conf!r} | sudo tee /etc/apache2/sites-available/000-default.conf",
                    "signal set": "balancer_conf",
                    "signal get": "disable_astra_mode",
                },
                "modules": {
                    "command": "sudo a2enmod proxy proxy_http proxy_balancer lbmethod_byrequests headers slotmem_shm",
                    "signal set": "modules",
                    "signal get": "balancer_conf",
                },
                "site": {
                    "command": "sudo a2ensite 000-default.conf && sudo apache2ctl configtest",
                    "signal set": "site",
                    "signal get": "modules",
                },
                "restart": {
                    "command": "sudo systemctl restart apache2.service",
                    "signal set": "restart",
                    "signal get": "site",
                },
                "keepalived": {
                    "command": (
                        keepalived_base_command
                        + f"interface=$(ip -o route get {lb_backup_ip} | awk '{{ for (i = 1; i <= NF; i++) if ($i == \"dev\") {{ print $(i + 1); exit }} }}'); "
                        "test -n \"$interface\"; "
                        + f"printf '%b' {keepalived_master_conf!r} | sudo tee /etc/keepalived/keepalived.conf; "
                        "sudo sed -i \"s/__INTERFACE__/$interface/g\" /etc/keepalived/keepalived.conf; "
                        "sudo systemctl enable keepalived.service; "
                        "sudo systemctl restart keepalived.service"
                    ),
                    "signal set": "keepalived",
                    "signal get": "restart",
                },
            },
            "testvm2": {
                "disable_astra_mode": {
                    "command": "sudo sed -i -e 's/# AstraMode on/AstraMode off/' -e 's/AstraMode on/AstraMode off/' /etc/apache2/apache2.conf",
                    "signal set": "disable_astra_mode",
                },
                "balancer_conf": {
                    "command": f"printf '%b' {balancer_conf!r} | sudo tee /etc/apache2/sites-available/000-default.conf",
                    "signal set": "balancer_conf",
                    "signal get": "disable_astra_mode",
                },
                "modules": {
                    "command": "sudo a2enmod proxy proxy_http proxy_balancer lbmethod_byrequests headers slotmem_shm",
                    "signal set": "modules",
                    "signal get": "balancer_conf",
                },
                "site": {
                    "command": "sudo a2ensite 000-default.conf && sudo apache2ctl configtest",
                    "signal set": "site",
                    "signal get": "modules",
                },
                "restart": {
                    "command": "sudo systemctl restart apache2.service",
                    "signal set": "restart",
                    "signal get": "site",
                },
                "keepalived": {
                    "command": (
                        keepalived_base_command
                        + f"interface=$(ip -o route get {lb_master_ip} | awk '{{ for (i = 1; i <= NF; i++) if ($i == \"dev\") {{ print $(i + 1); exit }} }}'); "
                        "test -n \"$interface\"; "
                        + f"printf '%b' {keepalived_backup_conf!r} | sudo tee /etc/keepalived/keepalived.conf; "
                        "sudo sed -i \"s/__INTERFACE__/$interface/g\" /etc/keepalived/keepalived.conf; "
                        "sudo systemctl enable keepalived.service; "
                        "sudo systemctl restart keepalived.service"
                    ),
                    "signal set": "keepalived",
                    "signal get": "restart",
                },
            },
            "testvm3": {
                "disable_astra_mode": {
                    "command": "sudo sed -i -e 's/# AstraMode on/AstraMode off/' -e 's/AstraMode on/AstraMode off/' /etc/apache2/apache2.conf",
                    "signal set": "disable_astra_mode",
                },
                "html": {
                    "command": backend_html_prepare_command,
                    "signal set": "html",
                    "signal get": "disable_astra_mode",
                },
                "backend_conf": {
                    "command": f"printf '%b' {remote_backend_conf!r} | sudo tee /etc/apache2/sites-available/000-default.conf",
                    "signal set": "backend_conf",
                    "signal get": "html",
                },
                "restart": {
                    "command": "sudo systemctl restart apache2.service",
                    "signal set": "restart",
                    "signal get": "backend_conf",
                },
            },
            "testvm4": {
                "disable_astra_mode": {
                    "command": "sudo sed -i -e 's/# AstraMode on/AstraMode off/' -e 's/AstraMode on/AstraMode off/' /etc/apache2/apache2.conf",
                    "signal set": "disable_astra_mode",
                },
                "html": {
                    "command": backend_html_prepare_command,
                    "signal set": "html",
                    "signal get": "disable_astra_mode",
                },
                "backend_conf": {
                    "command": f"printf '%b' {remote_backend_conf!r} | sudo tee /etc/apache2/sites-available/000-default.conf",
                    "signal set": "backend_conf",
                    "signal get": "html",
                },
                "restart": {
                    "command": "sudo systemctl restart apache2.service",
                    "signal set": "restart",
                    "signal get": "backend_conf",
                },
            },
        }

        self.provider.execute(commands=env_prepare, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        print ("\n\n\nПодготовка тестового окружения завершена\n\n\n")



    def start_test(self):
        """
        testvm1 - Apache load balancer + keepalived MASTER
        testvm2 - Apache load balancer + keepalived BACKUP
        testvm3/testvm4 - backend Apache
        testvm5 - ApacheBench client
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

        vip = A_BALANCE_VIP

        print ("\n\n\nПроверяем доступность VIP балансировщика\n\n\n")
        healthcheck = {
            "testvm5": {
                "vip": {
                    "command": f"curl -fsS --retry 30 --retry-delay 2 http://{vip}/ >/dev/null",
                    "signal set": "vip",
                },
            }
        }
        self.provider.execute(commands=healthcheck, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        print ("\n\n\nНачинаем ступенчатую нагрузку Apache balance\n\n\n")
        for concurrent in [1] + list(range(CONCURRENCY_STEP, MAX_CONCURRENCY, CONCURRENCY_STEP)):
            ab_test_command = f"""
                /usr/bin/ab -c {concurrent} -n {MAX_REQUESTS} -e {CSV_RESULTS_FILE} -g {PLOT_FILE} http://{vip}/ >> {AB_OUTPUT_FILE_BALANCE}
            """
            ab_test = {
                "testvm5": {
                    "run_test": {
                        "command": f"{ab_test_command}",
                        "signal set": "run_test",
                    },
                }
            }

            self.provider.execute(commands=ab_test, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)
        print ("\n\n\nСтупенчатая нагрузка Apache balance завершена\n\n\n")

        scp_results = {
            "testvm5": [
                {
                    "mode": "pull",
                    "path_host": f"{self.testdir}/summary_balance.txt",
                    "path_vm": f"{AB_OUTPUT_FILE_BALANCE}",
                },
            ]
        }
        self.provider.scp(scp_settings=scp_results, vms_dates=self.vms_data, vms_groups=self.vms_group, username=USERNAME, password=PASSWORD)

        if os.path.isfile(f"{self.testdir}/summary_balance.txt"):
            print (f"\n\n\nРезультаты успешно скопированы на сервер и расположены в {self.testdir}\n\n\n")
        else:
            print ("\n\nFail\nНе удалось скопировать результаты теста с ВМ\n\n\n")


    def preprocessing_results(self):

        sleep(120)
        print("\n\n\nЗабираем данные о ОС с ВМ\n\n\n")
        scp_vm_params = {
            "testvm1": [
                {
                    "mode": "pull",
                    "path_host": VM_INFONAME,
                    "path_vm": "/home/u/av.txt",
                },
                {
                    "mode": "pull",
                    "path_host": VM_KERNEL,
                    "path_vm": "/home/u/kernel.txt",
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

        with open(f"{self.testdir}/summary_balance.txt", encoding="utf-8") as result_file:
            result_content = result_file.read()

        records = []
        for block in re.split(r"This is ApacheBench", result_content):
            concurrency = re.search(r"Concurrency Level:\s+(\d+)", block)
            rps = re.search(r"Requests per second:\s+([\d.]+)", block)
            waiting = re.search(r"Waiting:\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", block)
            if not all([concurrency, rps, waiting]):
                continue
            records.append(
                {
                    "concurrency": int(concurrency.group(1)),
                    "requests_per_second": float(rps.group(1)),
                    "waiting_median_ms": float(waiting.group(4)),
                }
            )

        model = MathModel()
        model.add_criterion(
            "apache_balance_rps",
            iterations=[record["concurrency"] for record in records],
            values=[record["requests_per_second"] for record in records],
            weight=0.5,
            negative=False,
            bounds=(0.0, 140000.0),
        )
        model.add_criterion(
            "apache_balance_waiting",
            iterations=[record["concurrency"] for record in records],
            values=[record["waiting_median_ms"] for record in records],
            weight=0.5,
            negative=True,
            bounds=(0.0, 65000.0),
        )

        fixed_power = 0.9996180247850317
        result = model.total_rating(power=fixed_power)

        return round(result['total_rating'] / 100)
