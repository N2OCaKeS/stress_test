from ovpn_conf import RANGE, VRF_COUNT, PER_VRF
from allta import SystemCommands

sc = SystemCommands()

for j in range(1, VRF_COUNT + 1):
    try:
        vrf_name = f"vrf{j}"
        table_id = 1000 + j
        sc.cmd(f"ip link add {vrf_name} type vrf table {table_id}")
        sc.cmd(f"ip libk set dev {vrf_name} up")
    except Exception as e:
        print("Ошибка", e)

print(f"Создано {VRF_COUNT} таблиц.\nНа каждую таблицу - {PER_VRF} туннелей.")

for i in range(1, RANGE + 1):
    vrf_name = f"vrf{i}"
    tun_dev = f"tun{i}"
    cfg_file = f"/home/vagrant/clients_keys/tester{i - 1}/client.ovpn"

    sc.cmd(f"openvpn --config {cfg_file} --dev {tun_dev} --daemon")
    sc.cmd(f"ip link set dev {tun_dev} master {vrf_name}")
    sc.cmd(f"ip link set dev {tun_dev} up")
    sc.cmd(f"ip route add 10.8.0.1 dev {tun_dev} vrf {vrf_name} ")

    
    if i % 1000 == 0:
        print(f"Запущено в VRF {i} клиентов.")



