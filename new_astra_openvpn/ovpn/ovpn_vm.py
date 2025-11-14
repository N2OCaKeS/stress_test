from allta import Libvirt, LibvirtManager
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
)
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from typing import Optional, Dict, Any



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
            box = f"1.8.1.{mode}" if VERSION_OS.startswith("1.8") else f"1.7.5.{mode}"
            self.new_vms_dates = Libvirt.build(
                box=box, rc=rc, vms=VMS, vms_dates=VMS_DATES
            )
            print("Сохраняем данные о ВМ")
            with open(path, "w", encoding="UTF-8") as f:
                json.dump(self.new_vms_dates, f)
            print("Данные сохранены")
            print("ВМ созданы и настроены")

    def provision(self):
        print("\n\n\n Выполняется provision \n\n\n")

        Libvirt.set_hosts(domain="stress.rbt", vms_dates=self.new_vms_dates, username=USER, password=PASSWORD)    

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
                    "command": "tar -xzvf ovpn.subnet.tar.gz > /dev/null 2>&1 ",
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
                }                
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
        # print("\n\n\n Делаем снимок  \n\n\n")
        # LibvirtManager.Snapshot.create(vms=VMS, snapshot_name="Provision")
        # print("\n\n\n Cнимок успешно создан  \n\n\n")

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
                    "new": "keepalive 1 2",     
                }
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
                    "command": r"""sudo bash -lc 'astra-openvpn-server start || true'""",
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
        print(f"\n\n\n Сервер настроен \n\n\n")
        print()

    def start_test(self):
        print("\n\n\n Запускаем тест \n\n\n")

        client_count = 40
        client_per_minutes = 30
        start_client = {}
        for idx, i in enumerate(range(2, 6)):
            host = f"testvm{i}"
            client_start = idx * client_count

            cmd = (
                "sudo su -c 'ulimit -u 100000 && "
                "ulimit -n 100000 && "
                "ulimit -s 100000 && "
                f"python3.12 /home/u/loader.py --client_per_minutes {client_per_minutes} "
                f"--client_start {client_start} --client_count {client_count}'"
            )

            start_client[host] = {"run_perf": {"command": cmd}}

        Libvirt.execute(
            commands=start_client,
            vms_dates=self.new_vms_dates,
            vms_groups=VMS_GROUP,
            username=USER,
            password=PASSWORD,
        )

        print("\n\n\n Тест выполнен \n\n\n")

    def get_result(self):
        print("\n\n\n Получаем логи \n\n\n")
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
        print("\n\n\n Логи получены \n\n\n")

def analyze_clients_stats(
    csv_path: str,
    expected_rate: float = 2.0,      # ожидаемое число подключений в секунду
    show_plots: bool = True,         # показывать графики на экране
    save_prefix: Optional[str] = None  # если не None — сохраняем картинки в файлы
) -> Dict[str, Any]:
    """
    Анализирует CSV-файл вида:
        seconds,clients
        0,5
        1,5
        2,6
        ...

    Строит два графика:
    1) y = фактическое кол-во клиентов, x = ожидаемое (expected_rate * seconds)
    2) y = фактическое кол-во клиентов, x = время (seconds)

    Возвращает словарь со статистиками по clients.
    """

    df = pd.read_csv(csv_path)

    # базовая валидация
    if not {"seconds", "clients"}.issubset(df.columns):
        raise ValueError("Ожидаются колонки 'seconds' и 'clients' в CSV")

    valid_clients = df.loc[df["clients"] >= 0, "clients"]

    if valid_clients.empty:
        raise ValueError("Нет валидных значений clients (все < 0 или NaN)")

    df["expected_clients"] = df["seconds"] * expected_rate

    stats = {
        "count": int(valid_clients.count()),
        "min": int(valid_clients.min()),
        "max": int(valid_clients.max()),
        "mean": float(valid_clients.mean()),
        "median": float(valid_clients.median()),
        "std": float(valid_clients.std(ddof=1)),
        "p90": float(np.percentile(valid_clients, 90)),
        "p95": float(np.percentile(valid_clients, 95)),
        "p99": float(np.percentile(valid_clients, 99)),
        "last_value": int(valid_clients.iloc[-1]),
    }


    x = df.loc[df["clients"] >= 0, "seconds"].to_numpy()
    y = valid_clients.to_numpy()
    if len(x) >= 2:
        slope = np.polyfit(x, y, 1)[0]
        stats["approx_growth_per_second"] = float(slope)
    else:
        stats["approx_growth_per_second"] = None

    plt.figure()
    plt.plot(df["expected_clients"], df["clients"])
    plt.xlabel("Ожидаемое число клиентов (expected_rate * seconds)")
    plt.ylabel("Фактическое число клиентов")
    plt.title("Фактическое vs ожидаемое число клиентов")
    plt.grid(True)

    if save_prefix is not None:
        plt.savefig(f"{save_prefix}_expected_vs_actual.png", dpi=150, bbox_inches="tight")

    plt.figure()
    plt.plot(df["seconds"], df["clients"])
    plt.xlabel("Время, сек с начала измерений")
    plt.ylabel("Число клиентов")
    plt.title("Число клиентов во времени")
    plt.grid(True)

    if save_prefix is not None:
        plt.savefig(f"{save_prefix}_clients_vs_time.png", dpi=150, bbox_inches="tight")

    if show_plots:
        plt.show()
    else:
        plt.close("all")

    return stats




if __name__ == "__main__":
    stats = analyze_clients_stats(
        csv_path="stats.csv",
        expected_rate=2.0,                 # 2 подключения в секунду
        show_plots=True,                   # покажет графики
        save_prefix="clients_analysis"     # и сохранит PNG в файлы
    )

    print("Статистика по числу клиентов:")
    for k, v in stats.items():
        print(f"{k}: {v}")