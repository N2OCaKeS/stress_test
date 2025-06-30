from ovpn_conf import RANGE, DURATION_RATE, VMS, VMS_COUNT
from libs.libtests import Ovpn20k
from allta import SystemCommands
import os
import threading
from time import sleep
import argparse
#ovpn_cls = Ovpn20k()
sys_cls = SystemCommands()

class PerfVpn:
    def __init__(self, ranger=RANGE, duration=DURATION_RATE, vms=VMS, vms_count=VMS_COUNT):
        self.hostname = sys_cls.check_output_command("echo $HOSTNAME").split(".")[0]
        self.range = ranger
        self.vms = vms
        self.vms_count = vms_count
        self.step = self.range // self.vms_count

        self.vms_ranges = {
            vm: range(i * self.step, (i + 1) * self.step if i != self.vms_count - 1 else self.range)
            for i, vm in enumerate(vms)
        }

        self.rate = '1950K'
        self.duration = duration
        self.tun_number = 0
        self.tun_ip = ""
        self.server_ip = sys_cls.check_output_command("cat /etc/hosts").split()[3]
        self.iperf_log = "/var/log/iperf"
        self.counter = 0


    def run_iperf(self, tun_ip, tun_dev):
        try:
            sys_cls.cmd(f"iperf -c 10.8.0.1 -u --dualtest -b {self.rate} -t {self.duration} -B {tun_ip} -i 3 > {self.iperf_log}/{tun_dev}.log 2>&1 & ")
            print(f"{tun_dev} | Iperf | done")
        except Exception as e:
            print(f"{tun_dev} |  Iperf | error")


    def load_test(self):
        threads = []
        sys_cls.cmd("mkdir -p /var/log/openvpn/clients")  # Создаем директорию если нет
        
        try:
            for item in self.vms_ranges[self.hostname]:
                self.tun_number += 1
                tun_dev = f"tun{self.tun_number}"
                cfg_dir = f"/home/u/openvpn/clients_keys/tester{item}"
                log_file = f"/var/log/openvpn/clients/{tun_dev}.log"
                
                # Запускаем OpenVPN
                sys_cls.cmd(f"cd {cfg_dir} && openvpn --config client.ovpn --dev {tun_dev} --auth-nocache >> {log_file} 2>&1 &")
                print(f"{tun_dev} | OpenVPN запущен")

                # Ожидание создания туннеля
                tun_created = False
                for attempt in range(30):  # 30 попыток по 0.4 сек = 12 сек максимум
                    # 1. Проверяем существование интерфейса
                    if not os.path.exists(f"/sys/class/net/{tun_dev}"):
                        sleep(0.4)
                        continue
                    
                    # 2. Проверяем лог на успешное подключение
                    try:
                        with open(log_file, "r", encoding="utf-8") as f:
                            content = f.read()
                            if "Initialization Sequence Completed" in content:
                                tun_created = True
                                break
                    except IOError:
                        pass
                    
                    sleep(0.4)
                
                if not tun_created:
                    print(f"{tun_dev} | Туннель не создан после 30 попыток")
                    # Проверяем лог на ошибки
                    try:
                        with open(log_file, "r", encoding="utf-8") as f:
                            content = f.read()
                            if "TLS Error" in content:
                                print(f"{tun_dev} | Ошибка TLS в логе")
                            elif "AUTH_FAILED" in content:
                                print(f"{tun_dev} | Ошибка аутентификации")
                    except IOError:
                        pass
                    continue
                
                # Получаем IP туннеля
                try:
                    self.tun_ip = sys_cls.check_output_command(
                        f"ip -4 addr show dev {tun_dev} | grep inet"
                    ).split()[1].split("/")[0]
                    print(f"{tun_dev} | Успешно создан | IP: {self.tun_ip}")
                    self.counter += 1
                except Exception as e:
                    print(f"{tun_dev} | Ошибка получения IP: {str(e)}")
                    continue

                # Запускаем iperf
                thread = threading.Thread(target=self.run_iperf, args=(self.tun_ip, tun_dev))
                thread.start()
                threads.append(thread)

        except Exception as e:
            print("Критическая ошибка:", e)

        for t in threads:
            t.join()
        
        sleep(self.duration)
        print(f"\nИтоги:")
        print(f"Всего туннелей: {sys_cls.check_output_command('ls -la /sys/class/net | grep tun | wc -l')}")
        print(f"Успешных подключений: {self.counter}")


    def rm_connections(self):
        for i in range(1, 3):
            sys_cls.cmd(f"ip link delete vrf{i}")
        for i in range(1, self.range + 1):
            sys_cls.cmd(f"ip link delete tun{i}")

if __name__ == "__main__":

    perf_cls = PerfVpn()

    perf_cls.load_test()
    #perf_cls.rm_connections()