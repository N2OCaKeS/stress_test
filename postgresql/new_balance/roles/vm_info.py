import hashlib
from allta import Libvirt, VBox

PROVIDER = Libvirt()

with open('/etc/astra/build_version', 'r') as f:
    version = f.read().strip()
VERSION_OS = '.'.join(version.split('.')[:2])

if isinstance(PROVIDER, VBox):
    PGOOL_IP = '10.177.103.131' 
    if VERSION_OS == '1.7':
        VERSION_PG = '11'
        ETH_INTERFACE = 'eth0'

    elif VERSION_OS == '1.8':
        VERSION_PG = '15'
        ETH_INTERFACE = 'enp0s1'     

elif isinstance(PROVIDER, Libvirt):
    PGOOL_IP = '192.168.100.5'  

    if VERSION_OS == '1.7':
        VERSION_PG = '11'
        ETH_INTERFACE = 'eth0'
    elif VERSION_OS == '1.8':
        VERSION_PG = '15'
        ETH_INTERFACE = 'enp1s0'   


# ОТЛАДКА
# VERSION_PG = '15'    
# ETH_INTERFACE = 'enp1s0'     
# VERSION_OS='1.8'

USERNAME = 'u'
PASSWORD = '1'

VMS = ['database1', 'database2', 'database3', 'lbdb1',
       'lbdb2', 'lbdb3', 'dcfreeipa']  # Краткий список ВМ

VMS_DATES = {  # Полный список ВМ
    'database1': {'host-port': '22',
                  'ip': '10.0.0.11',
                  'ip_bridge': '10.177.103.111',
                  'cpu': '8',
                  'ram': '32768'},
    'database2': {'host-port': '22',
                  'ip': '10.0.0.12',
                  'ip_bridge': '10.177.103.112',
                  'cpu': '8',
                  'ram': '32768'},
    'database3': {'host-port': '22',
                  'ip': '10.0.0.13',
                  'ip_bridge': '10.177.103.113',
                  'cpu': '8',
                  'ram': '32768'},
    'lbdb1': {'host-port': '22',
              'ip': '10.0.0.41',
              'ip_bridge': '10.177.103.141',
              'cpu': '8',
              'ram': '32768'},
    'lbdb2': {'host-port': '22',
              'ip': '10.0.0.42',
              'ip_bridge': '10.177.103.142',
              'cpu': '8',
              'ram': '32768'},
    'lbdb3': {'host-port': '22',
              'ip': '10.0.0.43',
              'ip_bridge': '10.177.103.143',
              'cpu': '8',
              'ram': '32768'},
    'dcfreeipa': {'host-port': '22',
                  'ip': '10.0.0.10',
                  'ip_bridge': '10.177.103.110',
                  'cpu': '8',
                  'ram': '32768'}
}

VMS_GROUPS = {
    'all': ['database1', 'database2', 'database3', 'lbdb1', 'lbdb2', 'lbdb3', 'dcfreeipa'],
    'database': ['database1', 'database2', 'database3'],
    'load_balancer': ['lbdb1', 'lbdb2', 'lbdb3'],
    'replica': ['database2', 'database3'],
    'domain_client': ['database1', 'database2', 'database3', 'lbdb1', 'lbdb2', 'lbdb3']
}





DOMAIN = 'balance.rbt'
DOMAIN_ADMIN_USER = 'admin'
DOMAIN_ADMIN_PASSWORD = '12345678'
DOMAIN_USER_PASSWORD = '1'

POSTGRES_PORT = '5440'
POSTGRES_DATA_PATH = f'/var/lib/postgresql/{VERSION_PG}/contrprimer'

PGPOOL_HOSTNAME = f'pgpool.{DOMAIN}'
PGPOOL_CONFIG_PATH = '/etc/pgpool2/pgpool.conf'
PGPOOL_PCP_USER = 'pgpool'
PGPOOL_PASSWORD = '1'
PGPOOL_PASSWORD_MD5 = hashlib.md5((PGPOOL_PASSWORD).encode()).hexdigest()

PROVISION_PATH = './provision/provision.sh'
