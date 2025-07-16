import os
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
        self.batch_counter = self.range // 120
        self.wave_counter, self.wave_temp = 0, 0

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
        self.last_batch_time = None


    async def run_iperf(self, tun_ip, tun_dev, batch_number):
        try:
            proc = await asyncio.create_subprocess_shell(
                f"iperf -c 10.8.0.1 -u --dualtest -b {self.rate} -t {batch_number * 60 + 100} -B {tun_ip} -i 5 > {self.log}/iperf/clients_{self.hostname}/{tun_dev}.log 2>&1"
            )
            print(f"{tun_dev} | Iperf | запущен")

            return proc
        except Exception as e:
            print(f"{tun_dev} | Iperf | error: {str(e)}")


    async def run_tun(self, item):
        tun_dev = f"tun{item}"
        cfg_dir = f"/home/u/openvpn/clients_keys/tester{item}"
        log_file = f"{self.log}/openvpn/clients_{self.hostname}/clients_{tun_dev}.log"

        proc = await asyncio.create_subprocess_shell(
            f"cd {cfg_dir} && openvpn --config client.ovpn --dev {tun_dev} --auth-nocache >> {log_file} 2>&1"
        )
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


    @timer
    async def load_test(self):
        sys_cls.cmd(f"mkdir -p /var/log/openvpn/clients_{self.hostname}")
        sys_cls.cmd(f"mkdir -p /var/log/iperf")
        sys_cls.cmd(f"mkdir -p /var/log/iperf/clients_{self.hostname}")
        sys_cls.cmd(f"mkdir -p /var/log/active")

        total_tunnels = len(self.vms_ranges[self.hostname])
        batches = math.ceil(total_tunnels / self.cpm)
        
        print(f"Всего туннелей: {total_tunnels} | Будет {batches} партий по {self.cpm} туннелей")

        for batch_num in range(batches):
            batch_start = batch_num * self.cpm
            batch_end = (batch_num + 1) * self.cpm
            current_batch = list(self.vms_ranges[self.hostname])[batch_start:batch_end]

            if self.last_batch_time is not None:
                elapsed = (datetime.now() - self.last_batch_time).total_seconds()
                if elapsed < 60:
                    wait_time = 60 - elapsed
                    print(f"Ожидаем {wait_time:.1f} сек до следующей партии...")
                    await asyncio.sleep(wait_time)

            batch_start_time = datetime.now()
            self.last_batch_time = batch_start_time
            print(f"\nПартия {batch_num+1}/{batches} | Начало в {batch_start_time.strftime('%H:%M:%S')}")

            tasks = [self.run_tun(item) for item in current_batch]
            results = await asyncio.gather(*tasks)

            iperf_tasks = []
            for result in results:
                if result and isinstance(result, tuple):
                    tun_dev, tun_ip = result
                    iperf_tasks.append(self.run_iperf(tun_ip, tun_dev, self.batch_counter))
            self.batch_counter -= 1

            await asyncio.gather(*iperf_tasks)

            elapsed = (datetime.now() - batch_start_time).total_seconds()
            if elapsed < 60:
                await asyncio.sleep(60 - elapsed)

            active = self.count_active_tunnels()
            self.save_batch_result(batch_num=batch_num+1, active_tunnels=active)

            batch_duration = (datetime.now() - batch_start_time).total_seconds()
            print(f"Партия {batch_num+1} завершена за {batch_duration:.2f} сек")

        
        await asyncio.sleep(125)
        

    # Вспом ф-ции
    def count_active_tunnels(self):
        current_count = len([name for name in os.listdir('/sys/class/net') if name.startswith('tun')])
        delta = current_count - self.wave_temp
        self.wave_temp = current_count
        return delta
    

    def save_batch_result(self, batch_num, active_tunnels):
        with open(f"{self.log}/active/{self.hostname}_counts.csv", "a") as f:
            f.write(f"{batch_num},{active_tunnels}\n")


if __name__ == "__main__":
    
    perf_cls = AIOPerfVPN()

    with open("/home/av.txt", "r", encoding="UTF-8") as ver:
        temp = ver.read()
        av = ".".join(temp.split("."))[:3]
        change_conf_settings(host=perf_cls.hostname, av=av)
    if perf_cls.hostname != "testvm1":
        asyncio.run(perf_cls.load_test())
