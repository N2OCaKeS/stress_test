from ovpn_conf import RANGE, VRF_COUNT, PER_VRF
from libs.libtests import Ovpn20k
from allta import SystemCommands
import os
from time import sleep
#ovpn_cls = Ovpn20k()
sys_cls = SystemCommands()

class PerfVpn:
    def __init__(self, ranger=RANGE):
    
        self.tun_number = 0


        self.range = ranger
        self.server_ip = sys_cls.check_output_command("cat /etc/hosts").split()[3]
    def load_test(self):
    # Создание VRF
        try:
            for item in range(1, self.range + 1):
                self.tun_number += 1
                tun_dev = f"tun{self.tun_number}"
                cfg_dir = f"/home/vagrant/openvpn/clients_keys/tester{self.tun_number}"

                sys_cls.cmd(f"cd {cfg_dir} && openvpn --config client.ovpn --dev {tun_dev} --daemon")

                sleep(0.5)
                for _ in range(10):
                    if os.path.exists(f"/sys/class/net/{tun_dev}"):
                        break
                    sleep(0.4)
                else:
                    print(f"{tun_dev} не появился.")
                    continue

        except Exception as e:
            print("Ошибка", e)

        print(f"Создано {self.range} туннелей.")


    def rm_connections(self):
        for i in range(1, self.vrf_count + 1):
            sys_cls.cmd(f"ip link delete vrf{i}")
        for i in range(1, self.range + 1):
            sys_cls.cmd(f"ip link delete tun{i}")

if __name__ == "__main__":
    perf_cls = PerfVpn()

    perf_cls.load_test()
    #perf_cls.rm_connections()
