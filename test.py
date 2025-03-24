from allta import VBoxManager

pro = VBoxManager()

# vms = ['database1', 'database2', 'database3',
#        'lbdb1', 'lbdb2', 'lbdb3', 'dcfreeipa']

vms_dates = {
    'suac': {
        "host-port": "2025",
        "ip_bridge": "127.0.0.1",
        "cpus": "4",
        "memory": "32768",
        "disk":"40960"
    }
}

groups = {
    'database': ['suac']
}

apt_install = {
    'suac': ['postgresql'],
    # 'g_database': [f'postgresql-15'],
    # 'g_load_balaner': ['pgpool2'],
    # 'domain': ['astra-freeipa-server']
}



commands = {
    'suac': {
        'Mac postgresql': {
            'command':'sudo pdpl-user -i 63 -l 0:3 -c 0:8 postgresql',
            'signal set':'',
            'signal get':''
        },
        'Create New cluster': {
            'command':'sudo mkdir /etc/postgresql/15/contrprimer && chown postgresql:postgresql /etc/postgresql/15/contrprimer && sudo -u postgres initdb -D /etc/postgresql/15/contrprimer',
            'signal set':'',
            'signal get':''
        },
    }
}

sed = {
    'suac': {
        'path': '/etc/postgresql/15/contrprimer/pg_hba.conf',
        'old':'# IPv4 local connections:',
        'new':'' 
    }
}

pro.apt.install(apt_install, vms_dates) 
pro.execute(vm_dates=vms_dates, commands=commands, vms_groups=groups, username='u', password='1')
# pro.sed(sed, vms_dates)