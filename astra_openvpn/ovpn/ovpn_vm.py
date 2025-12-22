from allta import Libvirt, LibvirtManager, SystemCommands
import json
from pathlib import Path
from time import sleep
from ovpn.vm_conf import (
    VMS,
    VMS_DATES,
    VMS_GROUP,
    USER,
    PASSWORD,
    KERNEL,
    TEMPLATE_PATH,
    VERSION_OS,
    CLIENTS_TOTAL
)
import math
from ovpn.result import analyze_result


class Ovpn:
    def __init__(self):
        self.new_vms_dates = {}

    def build(self, rc: str, mode: str):
        path = Path("vms_dates.txt")

        if path.is_file():
            print("Ищем существующие ВМ")
            self.new_vms_dates = json.loads(path.read_text(encoding="utf-8"))
            print(self.new_vms_dates)
            print("ВМ найдены, восстанавливаем")
            LibvirtManager.Snapshot.revert(vms=VMS, snapshot_name="build")
            # LibvirtManager.Snapshot.revert(vms=VMS, snapshot_name="Provision")
            sleep(10)
            print("ВМ восстановлены")
        else:
            print("Выполняеся сборка ВМ")
            Libvirt.prepare()
            box = f"1.8.1.{mode}" if rc.startswith("1.8") else f"1.7.5.{mode}"
            self.new_vms_dates = Libvirt.build(
                box=box, rc=rc, vms=VMS, vms_dates=VMS_DATES, kernel=KERNEL
            )
            print("Сохраняем данные о ВМ")
            with open(path, "w", encoding="UTF-8") as f:
                json.dump(self.new_vms_dates, f)
            print("Данные сохранены")
            print("ВМ созданы и настроены")

    def provision(self):
        print("\n\n\n Выполняется provision \n\n\n")

        Libvirt.set_hosts(
            domain="stress.rbt",
            vms_dates=self.new_vms_dates,
            username=USER,
            password=PASSWORD,
        )

        scp_provision = {
            "g_all": [
                {
                    "mode": "push",
                    "path_host": f"{TEMPLATE_PATH}/env_provision.sh",
                    "path_vm": "/home/u/",
                },
                {
                    "mode": "push",
                    "path_host": f"{TEMPLATE_PATH}/statistic.py",
                    "path_vm": "/home/u/",
                },
            ],
            "g_clients_group": [
                {
                    "mode": "push",
                    "path_host": f"{TEMPLATE_PATH}/vpn.sh",
                    "path_vm": "/home/u/",
                },
                {
                    "mode": "push",
                    "path_host": f"{TEMPLATE_PATH}/loader.sh",
                    "path_vm": "/home/u/",
                },
                {
                    "mode": "push",
                    "path_host": f"{TEMPLATE_PATH}/loader.py",
                    "path_vm": "/home/u/",
                },
            ],
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
                    "signal get": ["cp config"],
                },
                "wget": {
                    "command": "wget ftp://10.177.103.10/openvpn/ovpn.subnet.tar.gz",
                    "signal set": "wget",
                    "signal get": "",
                },
                "unpack tar": {
                    "command": "tar -xzvf ovpn.subnet.tar.gz",
                    "signal set": "unpack",
                    "signal get": ["wget"],
                },
                "cp tar config to etc": {
                    "command": "sudo cp -r /home/u/openvpn /etc/",
                    "signal set": "cp config",
                    "signal get": ["unpack"],
                },
                "enable ip_forwards": {
                    "command": "sudo sysctl -w net.ipv4.ip_forward=1",
                    "signal set": "",
                    "signal get": ["cp config"],
                },
            },
        }

        Libvirt.execute(
            commands=provision,
            vms_dates=self.new_vms_dates,
            vms_groups=VMS_GROUP,
            username=USER,
            password=PASSWORD,
            timeout=15,
        )

        print("\n\n\n Provison выполнен \n\n\n")
        print("\n\n\n Настраиваем сеть  \n\n\n")
        LibvirtManager.Vm.stop(vms=VMS)
        
        print(SystemCommands.check_output_command("sudo sed -i 's/<forward mode=\"nat\"\/>/<forward mode=\"none\"\/>/' \"/vms/network.xml\""))
        print(SystemCommands.check_output_command("sudo virsh net-destroy test"))
        print(SystemCommands.check_output_command("virsh --connect qemu:///system net-create /vms/network.xml"))

        LibvirtManager.Vm.start(vms=VMS)
        print("\n\n\n Сеть настроена  \n\n\n")

    def server_settings(self):
        print("\n\n\n Настраивается сервер \n\n\n")
        cipher = ["grasshopper-cbc", "kuznyechik-cbc"]
        sed_server_settings = {
            "testvm1": [
                {
                    "path": "/etc/openvpn/server.conf",
                    "old": "CIPHER",
                    "new": cipher[0] if VERSION_OS.startswith("1.7") else cipher[1],
                },
                {
                    "path": "/etc/openvpn/server.conf",
                    "old": "status /var/log/openvpn/openvpn-status.log",
                    "new": "status /run/openvpn/openvpn-status.log",
                },
                {
                    "path": "/etc/openvpn/server.conf",
                    "old": "keepalive 15 120",
                    "new": "keepalive 3 6",
                },
            ]
        }
        Libvirt.sed(
            sed_conf=sed_server_settings,
            vms_dates=self.new_vms_dates,
            username=USER,
            password=PASSWORD,
        )
        # run_server = {
        #     "testvm1": {
        #         "server_settings": {
        #             "command": (
        #                 "sudo su -c 'ulimit -u 100000 && "
        #                 "ulimit -n 100000 && "
        #                 "ulimit -s 100000 && "
        #                 "python3.12 /home/u/netns_perf.py'"
        #             )
        #         }
        #     }
        # }

        run_server = {
            "testvm1": {
                "detect-astra-version": {
                    "command": r"""sudo bash -lc 'test -f /etc/astra/build_version && cut -d. -f1,2 /etc/astra/build_version | tr -d "[:space:]" > /run/astra_ver || echo unknown > /run/astra_ver'""",
                    "signal set": "srv:ver:ready",
                    "signal get": "",
                },
                "serverconf-apply-gost-for-1_8": {
                    "command": r"""sudo bash -lc 'VER="$(cat /run/astra_ver 2>/dev/null)"; test -f /etc/openvpn/server.conf && [[ "$VER" == 1.8* ]] && printf "\ndata-ciphers kuznyechik-cbc\nauth id-tc26-gost3411-12-512\n" >> /etc/openvpn/server.conf || true'""",
                    "signal set": "srv:conf:gost:done",
                    "signal get": ["srv:ver:ready"],
                },
                "serverconf-disable-ncp-for-1_7": {
                    "command": r"""sudo bash -lc 'VER="$(cat /run/astra_ver 2>/dev/null)"; test -f /etc/openvpn/server.conf && [[ "$VER" == 1.7* ]] && printf "\nncp-disable\n" >> /etc/openvpn/server.conf || true'""",
                    "signal set": "srv:conf:ncp:done",
                    "signal get": ["srv:conf:gost:done"],
                },
                "openvpn-server-start": {
                    "command": r"""sudo astra-openvpn-server start && sudo astra-openvpn-server status""",
                    "signal set": "srv:ovpn:up",
                    "signal get": ["srv:conf:ncp:done"],
                },
                "enable-ip-forwarding": {
                    "command": r"""sudo bash -lc 'sysctl -w net.ipv4.ip_forward=1'""",
                    "signal set": "srv:ipfwd:on",
                    "signal get": ["srv:ovpn:up"],
                },
                "iperf-unit-write": {
                    "command": r"""sudo bash -lc 'cat > /etc/systemd/system/iperf-server.service <<EOF
[Unit]
Description=iperf server
After=network.target

[Service]
ExecStart=/usr/bin/iperf -s -u -B 10.8.0.1 -i 5 -y C
StandardOutput=file:/var/log/iperf_server.log
StandardError=inherit
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF'""",
                    "signal set": "srv:iperf:unit:written",
                    "signal get": ["srv:ipfwd:on"],
                },
                "systemd-daemon-reload": {
                    "command": r"""sudo bash -lc 'systemctl daemon-reload'""",
                    "signal set": "srv:systemd:reloaded",
                    "signal get": ["srv:iperf:unit:written"],
                },
                "iperf-enable-service": {
                    "command": r"""sudo bash -lc 'systemctl enable iperf-server.service || true'""",
                    "signal set": "srv:iperf:enabled",
                    "signal get": ["srv:systemd:reloaded"],
                },
                "iperf-restart-service": {
                    "command": r"""sudo bash -lc 'systemctl restart iperf-server.service'""",
                    "signal set": "srv:iperf:up",
                    "signal get": ["srv:iperf:enabled"],
                },
                "iperf-check-port-5001": {
                    "command": r"""bash -lc 'netstat -tulpn 2>/dev/null | grep 5001 || echo "[WARN] iperf: порт 5001 не виден"'""",
                    "signal set": "server:ready",
                    "signal get": ["srv:iperf:up"],
                },
            },
            "g_all": {
                "install": {
                    "command": "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libgost-astra",
                    "signal set": "",
                    "signal get": ["testvm1" ,"server:ready"],
                }
            },
        }

        Libvirt.execute(
            commands=run_server,
            vms_dates=self.new_vms_dates,
            vms_groups=VMS_GROUP,
            username=USER,
            password=PASSWORD,
            timeout=15,
        )
        print("\n\n\n Сервер настроен \n\n\n")
        print()

    def start_test(self):
        print("\n\n\n Запускаем тест \n\n\n")

        client_count = int(CLIENTS_TOTAL / (len(list(VMS_DATES.keys())) - 1))
        client_per_minutes = 30

        total_seconds = math.ceil((client_count / client_per_minutes) * 60) + 180
        start_client = {
            "testvm1": {
                "get stats": {
                    "command": f"python3.12 /home/u/statistic.py --host 127.0.0.1 --port 7505 --interval 1 --duration {total_seconds} -o /home/u/stats.csv",
                    "signal set": "",
                    "signal get": "",
                },
            },
        }
        for idx, i in enumerate(range(2, 6)):
            host = f"testvm{i}"
            client_start = int(idx * client_count)

            cmd = (
                "sudo su -c 'ulimit -u 100000 && ulimit -n 100000 && ulimit -s 100000 && "
                f"python3.12 /home/u/loader.py --client_per_minutes {client_per_minutes} "
                f"--client_start {client_start} --client_count {client_count}'"
            )

            start_client[host] = {"run_perf": {"command": cmd, "nowait": True, "nowait_timeout": 15}}

        Libvirt.execute(
            commands=start_client,
            vms_dates=self.new_vms_dates,
            vms_groups=VMS_GROUP,
            username=USER,
            password=PASSWORD,
        )

        print("\n\n\n Получаем результаты \n\n\n")
        scp_pull = {
            "testvm1": [
                {
                    "mode": "pull",
                    "path_host": "/home/u/",
                    "path_vm": "/home/u/stats.csv",
                },
            ],
        }

        Libvirt.scp(
            scp_settings=scp_pull,
            vms_dates=self.new_vms_dates,
            vms_groups=VMS_GROUP,
            username=USER,
            password=PASSWORD,
        )
        print("\n\n\n Результаты получены \n\n\n")        

        print("\n\n\n Тест выполнен \n\n\n")

    def get_result(self):
        print("\n\n\n Обрабатываем результаты \n\n\n")

        result = analyze_result(
            csv_path="/home/u/stats.csv",
            tester_start=0,
            tester_count=CLIENTS_TOTAL,
        )

        print("\n\n\n Результаты обработаны \n\n\n")
        return result
        
