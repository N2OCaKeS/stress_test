from ovpn_conf import RANGE, VRF_COUNT, PER_VRF
from libs.libtests import Ovpn20k
from allta import SystemCommands
import os
from time import sleep
#ovpn_cls = Ovpn20k()
sys_cls = SystemCommands()

class PerfVpn:
    def __init__(self, vrf_count=VRF_COUNT, ranger=RANGE, per_vrf=PER_VRF):
    
        self.tun_number = 0
        self.vrf_list = []
        self.vrf_count = vrf_count
        self.range = ranger
        self.per_vrf = per_vrf
        self.server_ip = sys_cls.check_output_command("cat /etc/hosts").split()[3]
    def load_test(self):
    # Создание VRF
        for j in range(1, self.vrf_count + 1):
            try:
                vrf_name = f"vrf{j}"
                table_id = 1000 + j
                sys_cls.cmd(f"ip link add {vrf_name} type vrf table {table_id}")
                sys_cls.cmd(f"ip link set dev {vrf_name} up")
                print(f"Создан {vrf_name}")
                for item in range(1, self.per_vrf + 1):
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

                    sys_cls.cmd(f"ip link set dev {tun_dev} master {vrf_name}")
                    #sys_cls.cmd(f"ip vrf exec {vrf_name} ip route replace 10.8.0.1 dev {tun_dev}")
            except Exception as e:
                print("Ошибка", e)

        for vr in range(1, self.vrf_count + 1):
            sys_cls.cmd(f"ip vrf exec vrf{vr} ip route add default via 10.8.0.1")

        print(f"Создано {self.vrf_count} таблиц.\nНа каждую таблицу - {self.per_vrf} туннелей.")


    def rm_connections(self):
        for i in range(1, self.vrf_count + 1):
            sys_cls.cmd(f"ip link delete vrf{i}")
        for i in range(1, self.range + 1):
            sys_cls.cmd(f"ip link delete tun{i}")

if __name__ == "__main__":
    perf_cls = PerfVpn()

    perf_cls.load_test()
    #perf_cls.rm_connections()


