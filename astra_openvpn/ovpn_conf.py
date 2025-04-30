from libs.libovpn import run_command

USER = ["u", "askeladd"]

OVPN_PATH = f"/home/{USER[1]}/git/stress_test/astra_openvpn"
VENV_PATH = "/home/u/python/Python-3.12.1/venv/lib/python3.12/site-packages"


BOX_VERSIONS = [["1.7.5.o", "1.7.5.v", "1.7.5.s"], ["1.8.1.o", "1.8.1.v", "1.8.1.s"]]
BOXES = f"{OVPN_PATH}/box-config.json"
DATES = f"/home/{USER[1]}/dates_stand3.conf"

SYS_VERSION = run_command("cat /etc/astra/build_version | tr -d '[:space:]'")
SYS_KERNEL = run_command("uname -r | tr -d '[:space:]'")
SYS_VERSION_MOD = [SYS_VERSION+".o", SYS_VERSION+".v", SYS_VERSION+".s"]



VMS = ["vpn1", "vpn2", "balancer"]

VMS_DATES = {  # Полный список ВМ
    'vpn1': {'host-port': '22',
                  'ip_bridge': '10.177.103.120'},
    'vpn2': {'host-port': '22',
                  'ip_bridge': '10.177.103.121'},
    'balancer': {'host-port': '22',
                  'ip_bridge': '10.177.103.122'}
}

VMS_GROUPS = {
    "vpn's": ["vpn1", "vpn2"],
    'balancer': ["balancer"]
}
print(f'{SYS_VERSION}, {SYS_KERNEL}, {SYS_VERSION_MOD}')
with open('/etc/astra/build_version', 'r') as f:
    version = f.read().strip()
VERSION_OS = '.'.join(version.split('.')[:2])
if VERSION_OS == '1.7':
    VERSION_PG = '11'
    ETH_INTERFACE = 'eth0'
elif VERSION_OS == '1.8':
    VERSION_PG = '15'
    ETH_INTERFACE = 'enp0s3'
print(VERSION_OS)
