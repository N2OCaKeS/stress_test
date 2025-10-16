from allta import Libvirt, LibvirtManager
from ovpn.vm_conf import (
    VMS,
    VMS_DATES,
    VMS_GROUP,
    USER,
    PASSWORD,
    KERNEL,
    TEMPLATE_PATH,
    VERSION_OS,
)


class Ovpn:
    def __init__(self):
        self.new_vms_dates = {}

    def build(self, rc: str, mode: str):
        print("Выполняеся сборка ВМ")
        Libvirt.prepare()
        box = f"1.8.1.{mode}" if VERSION_OS.startswith("1.8") else f"1.7.5.{mode}"
        self.new_vms_dates = Libvirt.build(box=box, rc=rc, vms=VMS, vms_dates=VMS_DATES)
        print("ВМ успешно собраны, начинаю создание снимков")
        print(self.new_vms_dates)
        LibvirtManager.Snapshot.create(vms=VMS, snapshot_name="Build")
        print("Снимки созданы")

    def provision(self):
        print("Выполняется provision")
        print(self.new_vms_dates)
        scp_provision = {
            "g_all": [
                {
                    "mode": "push",
                    "path_host": f"{TEMPLATE_PATH}/env_provision.sh",
                    "path_vm": "/home/u/",
                },
                {
                    "mode": "push",
                    "path_host": f"{TEMPLATE_PATH}/netns_perf.py",
                    "path_vm": "/home/u/",
                },
            ]
        }

        Libvirt.scp(
            scp_settings=scp_provision,
            vms_dates=self.new_vms_dates,
            vms_groups=VMS_GROUP,
            username=USER,
            password=PASSWORD,
        )

        provision = {
            "g_all": {
                "provision": {
                    "command": "sudo bash /home/u/env_provision.sh",
                    "signal set": "provision",
                    "signal get": "",
                },
                "wget": {
                    "command": "wget -P /home/u/ ftp://10.177.103.10/openvpn/ovpn.subnet.tar.gz",
                    "signal set": "wget",
                    "signal get": "provision",
                },
                "unpack tar": {
                    "command": "tar -xzvf ovpn.subnet.tar.gz > /dev/null 2>&1 ",
                    "signal set": "unpack",
                    "signal get": "wget",
                },
                "cp tar config to etc": {
                    "command": "sudo cp -r /home/u/openvpn /etc/",
                    "signal set": "",
                    "signal get": "unpack",
                },
            }
        }
        print(self.new_vms_dates)
        Libvirt.execute(
            commands=provision,
            vms_dates=self.new_vms_dates,
            vms_groups=VMS_GROUP,
            username=USER,
            password=PASSWORD,
            timeout=15,
        )
        print("Provison выполнен")

    def server_settings(self):
        print("Настраивается сервер")
        cipher = ["grasshopper-cbc", "kuznyechik-cbc"]
        sed_server_settings = {
            "testvm1": [
                {
                    "path": "/etc/openvpn/server.conf",
                    "old": "CIPHER",
                    "new": cipher[0] if VERSION_OS == "1.7" else cipher[1],
                }
            ]
        }
        Libvirt.sed(
            sed_conf=sed_server_settings,
            vms_dates=self.new_vms_dates,
            username=USER,
            password=PASSWORD,
        )
        run_server = {
            "testvm1": {
                "server_settings": {
                    "command": (
                        "sudo ulimit -u 100000 && "
                        "sudo ulimit -n 100000 && "
                        "sudo ulimit -s 100000 && "
                        "sudo /home/u/python/Python-3.12.1/venv/bin/python /home/u/astra_openvpn/netns_perf.py"
                    )
                }
            }
        }
        Libvirt.execute(
            commands=run_server,
            vms_dates=self.new_vms_dates,
            vms_groups=VMS_GROUP,
            username=USER,
            password=PASSWORD,
            timeout=15,
        )

    def start_test(self):
        start_client = {
            "g_clients_group": {
                "run_perf": {
                    "command": (
                        "sudo ulimit -u 100000 && "
                        "sudo ulimit -n 100000 && "
                        "sudo ulimit -s 100000 && "
                        "sudo /home/u/python/Python-3.12.1/venv/bin/python /home/u/astra_openvpn/netns_perf.py"
                    )
                }
            }
        }
        Libvirt.execute(
            commands=start_client,
            vms_dates=self.new_vms_dates,
            vms_groups=VMS_GROUP,
            username=USER,
            password=PASSWORD,
        )

    def get_logs(self):
        scp_pull = {
            "testvm1": [
                {
                    "mode": "pull",
                    "path_host": "./results/raw/openvpn/openvpn.log",
                    "path_vm": "/var/log/openvpn/openvpn.log",
                },
                {
                    "mode": "pull",
                    "path_host": "./results/raw/iperf/iperf_server.log",
                    "path_vm": "/var/log/iperf_server.log",
                },
            ],
        }

        for i in range(2, 6):
            vm = f"testvm{i}"
            path_log = f"./results/raw/{vm}"
            scp_pull[vm] = [
                {
                    "mode": "pull",
                    "path_host": f"./results/raw/{path_log}",
                    "path_vm": "/var/log/openvpn",
                },
                {
                    "mode": "pull",
                    "path_host": f"./results/raw/{path_log}",
                    "path_vm": "/var/log/iperf",
                },
                {
                    "mode": "pull",
                    "path_host": f"./results/raw/{path_log}",
                    "path_vm": "/var/log/active",
                },
            ]

        Libvirt.scp(
            scp_settings=scp_pull,
            vms_dates=self.new_vms_dates,
            vms_groups=VMS_GROUP,
            username=USER,
            password=PASSWORD,
        )
