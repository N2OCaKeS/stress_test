import os
import math
import asyncio
from datetime import datetime
from libs.ovpnlib import timer
from ovpn_conf import RANGE, DURATION_RATE, VMS, VMS_COUNT, CONNECTIONS_PER_MINUTE, COLORS, sys_cls

class AIOPerfVPN:
    """
    Нагрузочный скрипт Openvpn-server с async.
     - Глобальные переменные теста в ovpn_conf.py

    """
    def __init__(self, 
                 ranger=RANGE, 
                 duration=DURATION_RATE, 
                 vms=VMS, 
                 vms_count=VMS_COUNT,
                 connections_per_minute=CONNECTIONS_PER_MINUTE,
                 colors=COLORS):
        self.range = ranger
        self.duration = duration
        self.vms = vms
        self.vms_count = vms_count - 1
        self.cpm = connections_per_minute
        self.hostname = sys_cls.check_output_command("echo $HOSTNAME").split(".")[0]
        self.colors = colors

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


    async def run_iperf(self, tun_ip, tun_dev):
        try:
            proc = await asyncio.create_subprocess_shell(
                f"iperf -c 10.8.0.1 -u --dualtest -b {self.rate} -t {self.duration} -B {tun_ip} -i 3 > {self.log}/iperf/{tun_dev}.log 2>&1"
            )
            print(f"{tun_dev} | Iperf | запущен")
            return proc
        except Exception as e:
            print(f"{tun_dev} | Iperf | error: {str(e)}")


    async def run_tun(self, item):
        tun_dev = f"tun{item}"
        cfg_dir = f"/home/u/openvpn/clients_keys/tester{item}"
        log_file = f"{self.log}/openvpn/clients/clients_{tun_dev}.log"

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
        sys_cls.cmd("mkdir -p /var/log/openvpn/clients")
        sys_cls.cmd("mkdir -p /var/log/iperf")
        
        total_tunnels = len(self.vms_ranges[self.hostname])
        batches = math.ceil(total_tunnels / self.cpm)
        
        print(f"Всего туннелей: {total_tunnels} | Будет {batches} партий по {self.cpm} туннелей")

        for batch_num in range(batches):
            batch_start = batch_num * self.cpm
            batch_end = (batch_num + 1) * self.cpm
            # current banch - текущий набор туннелей * cpm(clients_per_minute)
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
            
            # Создаем туннели текущей партии
            tasks = [self.run_tun(item) for item in current_batch]
            results = await asyncio.gather(*tasks)
            
            # Запускаем iperf для успешных туннелей
            iperf_tasks = []
            for result in results:
                if result and isinstance(result, tuple):
                    tun_dev, tun_ip = result
                    iperf_tasks.append(self.run_iperf(tun_ip, tun_dev))
            
            await asyncio.gather(*iperf_tasks)
            
            batch_duration = (datetime.now() - batch_start_time).total_seconds()
            print(f"Партия {batch_num+1} завершена за {batch_duration:.2f} сек")
        
        await asyncio.sleep(100)
        
        # Запись результатов
        # if os.path.exists(f"{self.log}/openvpn/{self.hostname}_result.csv"): # дозаписываем если есть
        #     with open(f"{self.log}/openvpn/{self.hostname}_result.csv", "a", encoding="UTF-8") as test_result:
        # else:
        active = sys_cls.check_output_command('ls -la /sys/class/net | grep tun | wc -l')
        with open(f"{self.log}/openvpn/{self.hostname}_result.csv", "w", encoding="UTF-8") as test_result:
            test_result.write("Задано туннелей,Всего туннелей,Успешных подключений,Результат теста\n") # создаем
            test_result.write(f"{self.vms_ranges[self.hostname]},"
                              f"{sys_cls.check_output_command('ls -la /sys/class/net | grep tun | wc -l')},"
                              f"{self.counter},"
                              f"{'PASS' if self.vms_ranges[self.hostname] == self.counter == active else 'FAIL'}")
            
        print(f"\nИтоги:")
        print(f"Задано туннелей: {self.vms_ranges[self.hostname]}")
        print(f"Всего туннелей: {active}")
        print(f"Успешных подключений: {self.counter}")


if __name__ == "__main__":
    perf_cls = AIOPerfVPN()
    asyncio.run(perf_cls.load_test())
