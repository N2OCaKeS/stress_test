import os
from sys import exit
import math
import asyncio
from datetime import datetime
from libs.ovpnlib import timer, change_conf_settings
from ovpn_conf import RANGE, VMS, VMS_COUNT, CONNECTIONS_PER_MINUTE, COLORS, sys_cls, VM_DIR


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
                 colors=COLORS,
                 vm_dir=VM_DIR):
        self.range = ranger
        self.vms = vms
        self.vms_count = vms_count - 1  
        self.cpm = connections_per_minute
        self.hostname = sys_cls.check_output_command("echo $HOSTNAME").split(".")[0]
        self.colors = colors
        self.wave_counter = self.range // 120
        self.wave_temp = 0
        self.vm_dir = vm_dir
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
        self.octet_counter = 255 // (self.vms_count+1) * int(list(self.hostname)[~0])


    async def run_iperf(self, tun_ip, tun_dev, wave_number, netns_name):
        try:
            proc = await asyncio.create_subprocess_shell(
                f'ip netns exec {netns_name} iperf -c 10.8.0.1 -u --dualtest -b {self.rate} -t {wave_number * 60 + 100} -B {tun_ip} -i 5 >> {self.log}/iperf/clients_{netns_name}.log 2>&1 &'
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

        # Без проверки - просто подождать пару секунд для старта
        await asyncio.sleep(2)

        try:
            # Получить IP из netns сразу
            tun_ip = sys_cls.check_output_command(
                f"ip netns exec {netns_name} ip -4 addr show dev {tun_dev} | grep inet"
            ).split()[1].split("/")[0]
            print(f"{tun_dev} | {self.colors['GREEN']}Pass{self.colors['RESET']} | IP: {tun_ip}")
            self.counter += 1
            return tun_dev, tun_ip
        except Exception as e:
            print(f"{tun_dev} | {self.colors['RED']}Fail{self.colors['RESET']} | Ошибка получения IP: {str(e)}")
            return tun_dev, None


    async def setup_netns(self, n):
        """Создает netns с уникальным IP"""
        netns = f"vpn{n}"
        if n % 254 == 0:
            self.octet_counter += 1

        try:
            cmds = [
                f"cd {self.vm_dir} && {self.vm_dir}/vpn.sh start {netns} 172.{self.octet_counter}.{n % 254 + 2} --no-tmux"
            ]
            for cmd in cmds:
                print(f"COMMAND: {cmd}")
                sys_cls.cmd(cmd)
            return f"vpn{n}"
            
        except Exception as e:
            print(f"Error creating {netns}: {str(e)}")
            # await self.cleanup_netns(n)
            return None


    @timer
    async def load_test(self):
        try:
            sys_cls.cmd(f"mkdir -p {self.log}/openvpn/clients_{self.hostname}")
            sys_cls.cmd(f"mkdir -p {self.log}/iperf/clients_{self.hostname}")
            sys_cls.cmd(f"mkdir -p {self.log}/active")

            # Инициализация iptables (один раз)
            sys_cls.cmd("sysctl -w net.ipv4.ip_forward=1")

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
                    netns_name = f"vpn{i}"
                    await self.setup_netns(i)
                    tun_tasks.append(self.run_tun(item=i, netns_name=netns_name))

                results = await asyncio.gather(*tun_tasks)
                
                print("Результаты tun_tasks:")
                for res in results:
                    print(repr(res))
                await asyncio.sleep(10)

                iperf_tasks = []
                for i, result in zip(current_wave, results):
                    netns_name = f"vpn{i}"
                    if result and isinstance(result, tuple):
                        tun_dev, tun_ip = result
                        iperf_tasks.append(self.run_iperf(tun_ip=tun_ip, 
                                                        tun_dev=tun_dev, 
                                                        wave_number=self.wave_counter, 
                                                        netns_name=netns_name))
                        print("Таска добавлена")
                print("Тут должен быть запуск iperf")
                await asyncio.gather(*iperf_tasks)
                self.wave_counter -= 1

                active = self.count_active_tunnels()
                self.save_wave_result(wave_num=wave+1,
                                    active_tunnels=active)
                print(f"Партия {wave+1} завершена за {round((datetime.now() - start_time).total_seconds(), 3)}")

            await asyncio.sleep(125)
        finally:
            cmds = [f"cd {self.vm_dir}/ && ./vpn.sh stop_all",
                    "pkill -f 'iperf|openvpn'"]
            for cmd in cmds:
                print(f"Команда очистки: {cmd}")
                sys_cls.cmd(cmd)
        

    # Вспом ф-ции
    def count_active_tunnels(self):
        current_count = len([name for name in os.listdir('/sys/class/net') if name.startswith('tun')])
        delta = current_count - self.wave_temp
        self.wave_temp = current_count
        return delta
    

    def save_wave_result(self, wave_num, active_tunnels):
        with open(f"{self.log}/active/{self.hostname}_counts.csv", "a") as f:
            f.write(f"{wave_num},{active_tunnels}\n")


    def cleanup_netns(self, n):
        cmds = [
            f"{self.vm_dir}/vpn.sh stop_all{n}"
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
        asyncio.run(perf_cls.load_test())
