from ovpn_conf import RANGE, VRF_COUNT, PER_VRF
from libs.libtests import Ovpn20k
from allta import SystemCommands
from time import sleep
#ovpn_cls = Ovpn20k()
sys_cls = SystemCommands()

start = 1
vrf_list = []
print(1)
# Создание VRF
for j in range(1, VRF_COUNT + 1):
    try:
        if j % 10 == 0:
            print(f"Запущено в VRF {j} клиентов.")
        vrf_name = f"vrf{j}"
        table_id = 1000 + j
        sys_cls.cmd(f"ip link add {vrf_name} type vrf table {table_id}")
        sys_cls.cmd(f"ip link set dev {vrf_name} up")
        print(2)
        for i in range(start, RANGE + 1):
            tun_dev = f"tun{i-1}"
            cfg_dir = f"/home/vagrant/clients_keys"
            ip = sys_cls.check_output_command("cat /etc/hosts").split()[5]
            sys_cls.cmd(f"sed -i 's/^remote 192\\.168\\.121\\.35 1194$/remote {ip} 1194/' /home/vagrant/clients_keys/tester{i}/client.ovpn")
            
            sys_cls.cmd(f"cd {cfg_dir}/tester{i} && openvpn --config client.ovpn --daemon")
            sys_cls.cmd(f"ip link set dev {tun_dev} master {vrf_name}")
            sys_cls.cmd(f"ip link set dev {tun_dev} up")
            sys_cls.cmd(f"ip route add 10.8.0.1 dev {tun_dev} vrf {vrf_name} ")
            sleep(1)
            if PER_VRF - i == 0:
                start = i + 1
                sys_cls.cmd("cd ..")
                break

    except Exception as e:
        print("Ошибка", e)

print(f"Создано {VRF_COUNT} таблиц.\nНа каждую таблицу - {PER_VRF} туннелей.")


