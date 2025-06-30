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
        
        try:
            for item in self.vms_ranges[self.hostname]:
                self.tun_number += 1
                tun_dev = f"tun{self.tun_number}"
                cfg_dir = f"/home/u/openvpn/clients_keys/tester{item}"
                
                sys_cls.cmd(f"cd {cfg_dir} && openvpn --config client.ovpn --dev {tun_dev} --auth-nocache >> /var/log/openvpn/clients/{tun_dev}.log 2>&1 &")

                sleep(0.5)
                with open(f"/var/log/openvpn/clients{tun_dev}", "r", encoding="UTF-8") as tun_log:
                    log_content = tun_log.read()
                    for _ in range(20):
                        if os.path.exists(f"/sys/class/net/{tun_dev}") and "Initialization Sequence Completed" in log_content:
                            self.couter += 1
                            break
                        sleep(0.4)
                    else:
                        print(f"{tun_dev} не появился.")
                        continue
                self.tun_ip = sys_cls.check_output_command(f"ip -4 addr show dev {tun_dev} | grep inet").split()[1].split("/")[0]

                thread = threading.Thread(target=self.run_iperf, args=(self.tun_ip, tun_dev))
                thread.start()
                threads.append(thread)

        except Exception as e:
            print("Ошибка", e)

        for t in threads:
            t.join()
        sleep(self.duration)
        print(f'Создано {sys_cls.check_output_command("ls -la /sys/class/net | grep tun | wc -l")} туннелей.', f"Проверено {self.counter} туннелей.", sep="\n")


    def rm_connections(self):
        for i in range(1, 3):
            sys_cls.cmd(f"ip link delete vrf{i}")
        for i in range(1, self.range + 1):
            sys_cls.cmd(f"ip link delete tun{i}")

if __name__ == "__main__":

    perf_cls = PerfVpn()

    perf_cls.load_test()
    #perf_cls.rm_connections()
