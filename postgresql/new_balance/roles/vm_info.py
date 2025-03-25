VMS = ['database1', 'database2', 'database3', 'lbdb1',
       'lbdb2', 'lbdb3', 'dcfreeipa']  # Краткий список ВМ

VMS_DATES = {  # Полный список ВМ
    'database1': {'host-port': '22',
                  'ip': '10.0.0.11',
                  'sshnum': '',
                  'ip_bridge': '10.177.103.111',
                  'cpus': '8',
                  'memory': '32768',
                  'disk': '40960'},
    'database2': {'host-port': '22',
                  'ip': '10.0.0.12',
                  'sshnum': '1',
                  'ip_bridge': '10.177.103.112',
                  'cpus': '8',
                  'memory': '32768',
                  'disk': '40960'},
    'database3': {'host-port': '22',
                  'ip': '10.0.0.13',
                  'sshnum': '2',
                  'ip_bridge': '10.177.103.113',
                  'cpus': '8',
                  'memory': '32768',
                  'disk': '40960'},
    'lbdb1': {'host-port': '22',
              'ip': '10.0.0.41',
              'sshnum': '3',
              'ip_bridge': '10.177.103.141',
              'cpus': '8',
              'memory': '32768',
              'disk': '40960'},
    'lbdb2': {'host-port': '22',
              'ip': '10.0.0.42',
              'sshnum': '4',
              'ip_bridge': '10.177.103.142',
              'cpus': '8',
              'memory': '32768',
              'disk': '40960'},
    'lbdb3': {'host-port': '22',
              'ip': '10.0.0.43',
              'sshnum': '5',
              'ip_bridge': '10.177.103.143',
              'cpus': '8',
              'memory': '32768',
              'disk': '40960'},
    'dcfreeipa': {'host-port': '22',
                  'ip': '10.0.0.10',
                  'sshnum': '7',
                  'ip_bridge': '10.177.103.110',
                  'cpus': '8',
                  'memory': '32768',
                  'disk': '40960'}
}

VMS_GROUPS = {
    'all': ['database1', 'database2', 'database3', 'lbdb1', 'lbdb2', 'lbdb3', 'dcfreeipa'],
    'database': ['database1', 'database2', 'database3'],
    'load_balaner': ['lbdb1', 'lbdb2', 'lbdb3'],
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


DOMAIN = 'balance.rbt'
DOMAIN_ADMIN_USER = 'admin'
DOMAIN_ADMIN_PASSWORD = '12345678'
DOMAIN_USER_PASSWORD = '1'
