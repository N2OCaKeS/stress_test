VMS = ['database1', 'database2', 'database3', 'lbdb1',
       'lbdb2', 'lbdb3', 'dcfreeipa']  # Краткий список ВМ

VMS_DATES = {  # Полный список ВМ
    'database1': {'host-port': '22',
                  'ip': '10.0.0.11',
                  'sshnum': '',
                  'ip_bridge': '10.177.103.111',
                  'cpus': '8',
                  'memory': '32768'},
    'database2': {'host-port': '22',
                  'ip': '10.0.0.12',
                  'sshnum': '1',
                  'ip_bridge': '10.177.103.112',
                  'cpus': '8',
                  'memory': '32768'},
    'database3': {'host-port': '22',
                  'ip': '10.0.0.13',
                  'sshnum': '2',
                  'ip_bridge': '10.177.103.113',
                  'cpus': '8',
                  'memory': '32768'},
    'lbdb1': {'host-port': '22',
              'ip': '10.0.0.41',
              'sshnum': '3',
              'ip_bridge': '10.177.103.141',
              'cpus': '8',
              'memory': '32768'},
    'lbdb2': {'host-port': '22',
              'ip': '10.0.0.42',
              'sshnum': '4',
              'ip_bridge': '10.177.103.142',
              'cpus': '8',
              'memory': '32768'},
    'lbdb3': {'host-port': '22',
              'ip': '10.0.0.43',
              'sshnum': '5',
              'ip_bridge': '10.177.103.143',
              'cpus': '8',
              'memory': '32768'},
    'dcfreeipa': {'host-port': '22',
                  'ip': '10.0.0.10',
                  'sshnum': '7',
                  'ip_bridge': '10.177.103.110',
                  'cpus': '8',
                  'memory': '32768'}
}

VMS_GROUPS = {
    'all': ['database1', 'database2', 'database3', 'lbdb1', 'lbdb2', 'lbdb3', 'dcfreeipa'],
    'database': ['database1', 'database2', 'database3'],
    'load_balancer': ['lbdb1', 'lbdb2', 'lbdb3'],
    'replica': ['database2', 'database3'],
    'domain_client': ['database1', 'database2', 'database3', 'lbdb1', 'lbdb2', 'lbdb3']
}


with open('/etc/astra/build_version', 'r') as f:
    version = f.read().strip()
VERSION_OS = '.'.join(version.split('.')[:2])
if VERSION_OS == '1.7':
    VERSION_PG = '11'
elif VERSION_OS == '1.8':
    VERSION_PG = '15'
VERSION_PG = '11'


DOMAIN = 'balance.rbt'
DOMAIN_ADMIN_USER = 'admin'
DOMAIN_ADMIN_PASSWORD = '12345678'
DOMAIN_USER_PASSWORD = '1'

POSTGRES_PORT = '5440'
POSTGRES_DATA_PATH = f'/var/lib/postgresql/{VERSION_PG}/contrprimer'