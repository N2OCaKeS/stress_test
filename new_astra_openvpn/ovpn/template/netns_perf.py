import time
import math
import asyncio
from datetime import datetime
from allta import SystemCommands
from os.path import exists

sys_cls = SystemCommands()
RANGE = 1200
VMS_COUNT = 4
start_count = 2
base_name = "testvm"
VMS = [f"{base_name}{i+start_count}" for i in range(VMS_COUNT)]
COLORS = {
    "GREEN": "\033[32m",
    "RED": "\033[31m",
    "RESET": "\033[0m"
}
RC = sys_cls.check_output_command("cat /etc/astra/build_version | tr -d '[:space:]'")
VERSION_OS = ".".join(RC.split(".")[:2])

def timer(func):
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        elapsed_time = (end_time - start_time) / 60
        print(f"Затрачено времени: {round(elapsed_time, 4)} мин")
        return result
    return wrapper


async def cmd_async(command: str):
    """
    Асинхронно выполняет системную команду.

    Args:
        command (str): Команда для выполнения.

    Returns:
        str: Вывод команды (stdout + stderr).
    """
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        raise RuntimeError(f"Command failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def check_output_command_async(command: str) -> str:
    """
    Асинхронно выполняет команду и возвращает её вывод.

    Args:
        command (str): Команда для выполнения

    Returns:
        str: Вывод команды или текст ошибки

    Raises:
        RuntimeError: Если команда завершилась с ошибкой
    """
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=1024*1024  # 1MB buffer
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip()
        raise RuntimeError(f"Command failed: {error_msg}")

    return stdout.decode().strip()


@timer
def change_conf_settings(host, av):
    sys_cls.cmd("""
cat > /etc/systemd/system/iperf-server.service <<EOF
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
EOF
""")

    if av == "1.8":
        if host != "testvm1":
            for i in range(0, 10000):  # От tester0 до tester9999
                sys_cls.cmd(f"cd /home/u/openvpn/clients_keys/tester{i} && "
                            "sed -i 's/grasshopper-cbc/kuznyechik-cbc/g' client.ovpn")
                sys_cls.cmd('echo -e "\ndata-ciphers kuznyechik-cbc\nauth id-tc26-gost3411-12-512\n"'
                            f'>> /home/u/openvpn/clients_keys/tester{i}/client.ovpn')
                
        if exists("/etc/openvpn/server.conf"):    
            sys_cls.cmd('echo -e "\ndata-ciphers kuznyechik-cbc\nauth id-tc26-gost3411-12-512\n"'
                        '>> /etc/openvpn/server.conf')
            sys_cls.cmd("astra-openvpn-server start")
            sys_cls.cmd("systemctl daemon-reload && systemctl restart iperf-server.service")
            #sys_cls.cmd("pkill -f iperf && echo "" > /var/log/iperf_server.log && systemctl restart iperf-server.service")
            print(sys_cls.check_output_command("netstat -tulpn | grep 5001")) 
        else: "Конфигурация сервера не найдена в /etc/hosts"

    elif av == "1.7":
        if host != "testvm1":
            for i in range(0, 10000):
                sys_cls.cmd("echo -e '\nncp-disable\n'"
                            f">> /home/u/openvpn/clients_keys/tester{i}/client.ovpn")
        if exists("/etc/openvpn/server.conf"):
            sys_cls.cmd("echo -e '\nncp-disable\n'"
                        ">> /etc/openvpn/server.conf")
            sys_cls.cmd("astra-openvpn-server start")
            sys_cls.cmd("systemctl daemon-reload && systemctl start iperf-server")
            print(sys_cls.check_output_command("netstat -tulpn | grep 5001"))


class AIOPerfVPN:
    """
    Нагрузочный скрипт Openvpn-server с async.
     - Глобальные переменные теста в ovpn_conf.py

    """
    def __init__(self,
                 ranger=RANGE, 
                 vms=VMS, 
                 vms_count=VMS_COUNT,
                 connections_per_minute=120 // (VMS_COUNT),
                 colors=COLORS,
                 vm_dir="/home/u",
                 astra_version=VERSION_OS):
        self.range = ranger

        self.vms_count = vms_count - 1  
        self.cpm = connections_per_minute
        self.hostname = sys_cls.check_output_command("echo $HOSTNAME").split(".")[0]
        self.astra_version = astra_version
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
        
        self.rate = "250K"
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
                f'ip netns exec {netns_name} iperf -c 10.8.0.1 -u --dualtest -b {self.rate} -t {wave_number * 60 + 100} -B {tun_ip} -i 5 >> {self.log}/iperf/clients_{self.hostname}/clients_{netns_name}.log 2>&1 &'
            )
            print(f"{tun_dev} | Iperf | запущен")

            return proc
        except Exception as e:
            print(f"{tun_dev} | Iperf | error: {str(e)}")


    async def run_tun(self, item, netns_name):
        tun_dev = f"tun{item}"
        cfg_dir = f"/home/u/openvpn/clients_keys/tester{item}"
        log_file = f"{self.log}/openvpn/clients_{self.hostname}/clients_{tun_dev}.log"

        try:
            # Запускаем OpenVPN
            proc = await asyncio.create_subprocess_shell(
                f'ip netns exec {netns_name} bash -c "cd {cfg_dir} && openvpn --config client.ovpn --dev {tun_dev} --auth-nocache >> {log_file} 2>&1 &"'
            )
            print(f"{tun_dev} | OpenVPN запущен")

            # Ждем и проверяем интерфейс с таймаутом
            tun_ip = None
            start_time = asyncio.get_event_loop().time()
            
            while asyncio.get_event_loop().time() - start_time < 10:  # 10 сек таймаут
                try:
                    output = await check_output_command_async(
                        f"ip netns exec {netns_name} ip -4 addr show dev {tun_dev} | grep inet"
                    )
                    tun_ip = output.split()[1].split("/")[0]
                    break
                except (RuntimeError, IndexError):
                    await asyncio.sleep(0.5)  # Проверяем каждые 0.5 сек

            if tun_ip:
                print(f"{tun_dev} | {self.colors['GREEN']}Pass{self.colors['RESET']} | IP: {tun_ip}")
                self.counter += 1
                return tun_dev, tun_ip, True
            else:
                raise RuntimeError("Не удалось получить IP адрес")
                
        except Exception as e:
            print(f"{tun_dev} | {self.colors['RED']}Fail{self.colors['RESET']} | Ошибка: {str(e)}")
            return tun_dev, None, False 


    async def setup_netns(self, n):
        """Создает netns с уникальным IP"""
        netns = f"vpn{n}"
        if n % 254 == 0:
            self.octet_counter += 1

        try:
            cmds = [
                f"cd {self.vm_dir} && ./vpn.sh start {netns} 172.{self.octet_counter}.{n % 254 + 2} --no-tmux"
            ]
            for cmd in cmds:
                print(f"COMMAND: {cmd}")
                await cmd_async(cmd)
            return f"vpn{n}"
            
        except Exception as e:
            print(f"Error creating {netns}: {str(e)}")
            # await self.cleanup_netns(n)
            return None


    @timer
    async def setup_all_netns(self):
        """Создает все netns перед началом теста"""
        total_tunnels = len(self.vms_ranges[self.hostname])
        print(f"Создание {total_tunnels} netns...")
        
        tasks = []
        for i in self.vms_ranges[self.hostname]:
            netns_name = f"vpn{i}"
            tasks.append(self.setup_netns(i))
            
            # Ограничиваем параллелизм
            if len(tasks) >= 20:
                await asyncio.gather(*tasks)
                tasks = []
        
        if tasks:
            await asyncio.gather(*tasks)


    @timer
    async def load_test(self):
        try:
            sys_cls.cmd(f"mkdir -p {self.log}/openvpn/clients_{self.hostname}")
            sys_cls.cmd(f"mkdir -p {self.log}/iperf/clients_{self.hostname}")
            sys_cls.cmd(f"mkdir -p {self.log}/active")

            await self.setup_all_netns()

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
                    # await self.setup_netns(i)
                    tun_tasks.append(self.run_tun(item=i, netns_name=netns_name))
        
                results = await asyncio.gather(*tun_tasks)
                
                print("Результаты tun_tasks:")
                for res in results:
                    print(repr(res))
                self.count_active_tunnels(results)
                active_tunnels = self.count_active_tunnels(results)
                print(f"Активных туннелей!")

                await asyncio.sleep(10)

                iperf_tasks = []
                for i, result in zip(current_wave, results):
                    netns_name = f"vpn{i}"
                    if result and isinstance(result, tuple):
                        tun_dev, tun_ip, status = result
                        if status:
                            iperf_tasks.append(self.run_iperf(tun_ip=tun_ip, 
                                                            tun_dev=tun_dev, 
                                                            wave_number=self.wave_counter, 
                                                            netns_name=netns_name))
                        print("Таска добавлена")
                print("Тут должен быть запуск iperf")
                await asyncio.gather(*iperf_tasks)
                self.wave_counter -= 1

                self.save_wave_result(wave_num=wave+1,
                                    active_tunnels=active_tunnels)
                print(f"Партия {wave+1} завершена за {round((datetime.now() - start_time).total_seconds(), 3)}")

            await asyncio.sleep(155)
        finally:
            cmds = [f"cd {self.vm_dir}/ && ./vpn.sh stop_all",
                    "pkill -f 'iperf|openvpn'"]
            for cmd in cmds:
                print(f"Команда очистки: {cmd}")
                sys_cls.cmd(cmd)


    # Вспом ф-ции
    def count_active_tunnels(self, results_def_run_tun):
        current_count = 0
        for result in results_def_run_tun:
            if result[2]:
                current_count += 1
        return current_count


    

    def save_wave_result(self, wave_num, active_tunnels):
        with open(f"{self.log}/active/{self.hostname}_counts.csv", "a") as f:
            f.write(f"{wave_num},{active_tunnels}\n")


    def cleanup_netns(self, n):
        cmds = [
            f"cd {self.vm_dir} && ./vpn.sh stop_all{n}"
        ]

        for cmd in cmds:
            sys_cls.cmd(cmd)


if __name__ == "__main__":
    
    perf_cls = AIOPerfVPN()

    change_conf_settings(host=perf_cls.hostname, av=perf_cls.astra_version)

    if perf_cls.hostname != "testvm1":
        asyncio.run(perf_cls.load_test())

