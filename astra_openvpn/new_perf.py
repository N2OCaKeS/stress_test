import os
from sys import exit
import math
import asyncio
from datetime import datetime
from libs.ovpnlib import timer, change_conf_settings
from ovpn_conf import RANGE, VMS, VMS_COUNT, CONNECTIONS_PER_MINUTE, COLORS, sys_cls


class AIOPerfVPN:
    """
    Нагрузочный скрипт Openvpn-server с async.
     - Глобальные переменные теста в ovpn_conf.py

    """
    def __init__(self,
                 ranger=RANGE, 
                 vms=VMS, 
                 vms_count=VMS_COUNT,
                 connections_per_minute=CONNECTIONS_PER_MINUTE,
                 colors=COLORS):
        self.range = ranger
        self.vms = vms
        self.vms_count = vms_count - 1  
        self.cpm = connections_per_minute
        self.hostname = sys_cls.check_output_command("echo $HOSTNAME").split(".")[0]
        self.colors = colors
        self.wave_counter = self.range // 120
        self.wave_temp = 0

        # range for each VM
        self.step = self.range // self.vms_count
        self.vms_ranges = {
            vm: range(i * self.step, (i + 1) * self.step if i != self.vms_count - 1 else self.range)
            for i, vm in enumerate(vms)
        }
        
        self.rate = "1950K"
        self.log = "/var/log"
        self.server_ip = sys_cls.check_output_command("cat /etc/hosts").split()[3]
        self.tun_ip, self.tun_number = "", 0
        self.counter = 0
        self.aio_lock = asyncio.Lock()
        self.last_wave_time = None
        self.octet_counter = 0


    async def run_iperf(self, tun_ip, tun_dev, wave_number, netns_name):
        try:
            proc = await asyncio.create_subprocess_shell(
                f'ip netns exec {netns_name} bash -c' 
                f'"iperf -c 10.8.0.1 -u --dualtest -b {self.rate} -t {wave_number * 60 + 100} -B {tun_ip} -i 5 > {self.log}/iperf/clients_{self.hostname}/{tun_dev}.log 2>&1"'
            )
            print(f"{tun_dev} | Iperf | запущен")

            return proc
        except Exception as e:
            print(f"{tun_dev} | Iperf | error: {str(e)}")


    async def run_tun(self, item, netns_name):
        tun_dev = f"tun{item}"
        cfg_dir = f"/home/u/openvpn/clients_keys/tester{item}"
        log_file = f"{self.log}/openvpn/clients_{self.hostname}/clients_{tun_dev}.log"

        proc = await asyncio.create_subprocess_shell(
            f'ip netns exec {netns_name} bash -c "cd {cfg_dir} && openvpn --config client.ovpn --dev {tun_dev} --auth-nocache >> {log_file} 2>&1 &"'        )
        print(f"{tun_dev} | OpenVPN запущен")

        tun_created = False
        for attempt in range(30):
            if not os.path.exists(f"/sys/class/net/{tun_dev}"):
                await asyncio.sleep(0.4)
                continue
            
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    if "Initialization Sequence Completed" in f.read():
                        tun_created = True
                        break
            except IOError:
                pass
            
            await asyncio.sleep(0.4)
        
        if not tun_created:
            print(f"{tun_dev} | Туннель не создан после 30 попыток")
            return None
        
        try:
            self.tun_ip = sys_cls.check_output_command(
                f"ip -4 addr show dev {tun_dev} | grep inet"
            ).split()[1].split("/")[0]
            print(f"{tun_dev} | {self.colors['GREEN']}Pass{self.colors['RESET']} | IP: {self.tun_ip}")
            self.counter += 1
            return tun_dev, self.tun_ip
        except Exception as e:
            print(f"{tun_dev} | {self.colors['RED']}Fail{self.colors['RESET']} | Ошибка получения IP: {str(e)}")
            return None


    async def setup_netns(self, n):
        """Создает netns с уникальным IP из всего диапазона 172.20.0.0/16"""
        netns_name = f"netns{n}"

        if n % 127 == 0:
            self.octet_counter += 1

        
        # Генерация IP
        octet3 = (self.octet_counter // 127) % 127
        octets_root4 = [i for i in range(2, 256) if i % 2 == 0] # четные - основной 
        octets_netns4 = [i for i in range(2, 256) if i % 2 != 0] # нечетные - namespace
        
        root_ip = f"172.20.{octet3}.{octets_root4[n % 128]}/12"
        client_ip = f"172.20.{octet3}.{octets_netns4[n % 128]}/12"
        client_veth = f"veth-{octet3}-{octets_netns4[n % 128]}"  # Уникальное имя


        try:
            # Конфигурация netns
            cmds = [
                # namespace и veth пара
                f"ip netns add {netns_name}",
                f"ip link add {client_veth}-host type veth peer name {client_veth}-ns",
                f"ip link set {client_veth}-ns netns {netns_name}",
                # ip и вкл интерфейсов
                f"ip addr add {root_ip} dev {client_veth}-host",
                f"ip link set {client_veth}-host up",
                f"ip netns exec {netns_name} ip addr add {client_ip} dev {client_veth}-ns",
                f"ip netns exec {netns_name} ip link set {client_veth}-ns up",
                f"ip netns exec {netns_name} ip link set lo up",
                # iptables
                f"iptables -A FORWARD -i enp1s0 -o {client_veth}-host -m state --state RELATED,ESTABLISHED -j ACCEPT",
                f"iptables -A FORWARD -i {client_veth}-host -o enp1s0 -j ACCEPT",
                # default via
                f"ip netns exec {netns_name} ip route add default via 172.20.0.1"
            ]
            for cmd in cmds:
                sys_cls.cmd(cmd)
            return netns_name
            
        except Exception as e:
            print(f"Error creating {netns_name}: {str(e)}")
            await self.cleanup_netns(n)
            return None


    @timer
    async def load_test(self):
        sys_cls.cmd(f"mkdir -p /var/log/openvpn/clients_{self.hostname}")
        sys_cls.cmd(f"mkdir -p /var/log/iperf/clients_{self.hostname}")
        sys_cls.cmd(f"mkdir -p /var/log/active")

        # Инициализация iptables (один раз)
        sys_cls.cmd("sysctl -w net.ipv4.ip_forward=1")
        sys_cls.cmd("iptables -t nat -A POSTROUTING -s 172.20.0.0/16 -o enp1s0 -j MASQUERADE")

        total_tunnels = len(self.vms_ranges[self.hostname])
        waves = math.ceil(total_tunnels / self.cpm)
        print(f"Всего туннелей: {total_tunnels} | Будет {waves} волн по {self.cpm} клиентов.")

        for wave in range(waves):
            start = wave * self.cpm
            end = (wave + 1) * self.cpm
            current_wave = list(self.vms_ranges[self.hostname])[start:end]

            if self.last_wave_time is not None:
                elapsed = (datetime.now() - self.last_wave_time).total_seconds()
                if elapsed < 60:
                    await asyncio.sleep(60 - elapsed)

            start_time = datetime.now()
            self.last_wave_time = start_time

            print(f"\nПартия {wave+1}/{waves} | Начало в {start_time.strftime('%H:%M:%S')}")

            tun_tasks = []
            for i in current_wave:
                netns_name = f"netns{i}"
                await self.setup_netns(i)
                tun_tasks.append(self.run_tun(item=i, netns_name=netns_name))

            results = await asyncio.gather(*tun_tasks)

            iperf_tasks = []
            for result in results:
                if result and isinstance(result, tuple):
                    tun_dev, tun_ip = result
                    iperf_tasks.append(self.run_iperf(tun_ip=tun_ip, 
                                                      tun_dev=tun_dev, 
                                                      wave_number=self.wave_counter, 
                                                      netns_name=f"netns{i}"))
            await asyncio.gather(*iperf_tasks)
            self.wave_counter -= 1

            active = self.count_active_tunnels()
            self.save_wave_result(wave_num=wave+1,
                                  active_tunnels=active)
            print(f"Партия {wave+1} завершена за {round((datetime.now() - start_time).total_seconds(), 3)}")

        await asyncio.sleep(125)
        

    # Вспом ф-ции
    def count_active_tunnels(self):
        current_count = len([name for name in os.listdir('/sys/class/net') if name.startswith('tun')])
        delta = current_count - self.wave_temp
        self.wave_temp = current_count
        return delta
    

    def save_wave_result(self, wave_num, active_tunnels):
        with open(f"{self.log}/active/{self.hostname}_counts.csv", "a") as f:
            f.write(f"{wave_num},{active_tunnels}\n")

    
    def _init_root_veth():
        """Один раз настраивает dev для взаимодействия с netns в пространстве root"""
        if not os.path.exists("/sys/class/net/gw-veth"):
            sys_cls.cmd("ip link add gw-veth type veth peer name gw-veth-ns")
            sys_cls.cmd("ip addr add 172.20.0.1/16 dev gw-veth")
            sys_cls.cmd("ip link set gw-veth up")
            sys_cls.cmd("sysctl -w net.ipv4.ip_forward=1")
            sys_cls.cmd("iptables -t net -A POSTROUTING -s 172.20.0.0/16 -o enp1s0 -j MASQUERADE")


    def cleanup_netns(n):
        """Надёжная очистка одного неймспейса"""
        netns_name = f"netns{n}"
        veth0 = f"veth{n}-0"

        cmds = [
            f"ip link delete {veth0} 2>/dev/null || true",
            f"ip netns delete {netns_name} 2>/dev/null || true"
        ]

        for cmd in cmds:
            sys_cls.cmd(cmd)


if __name__ == "__main__":
    
    perf_cls = AIOPerfVPN()

    with open("/home/av.txt", "r", encoding="UTF-8") as ver:
        temp = ver.read()
        av = ".".join(temp.split("."))[:3]
        change_conf_settings(host=perf_cls.hostname, av=av)
    if perf_cls.hostname != "testvm1":
        for i in range(100):
            perf_cls.cleanup_netns(i)
        asyncio.run(perf_cls.load_test())