from vbox import VBox
from base_commands._set_hosts import _set_hosts

vms_date = {
    'suac': {
        'host-port': '2025',
        'ip': '0.0.0.0',
        'sshnum': '',
        'ip_bridge':'127.0.0.1',
        'cpus':'4',
        'memory':'4096'
    },
    'susrv': {
        'host-port': '2023',
        'ip': '0.0.0.0',
        'sshnum': '',
        'ip_bridge':'127.0.5.1',
        'cpus':'4',
        'memory':'4096'
    },
}

groups = {
    'databases': ['suac', 'susrv'],
    'domen': ['dm1', 'dm2']
}

commands = {
    'g_databases': { # имя хоста или имя группы хостов на которых нужно выполнить команду имя группы будет называться с g_ в начале
        'test signal set': { # имя задачи
            'command':'sudo apt-get update & sudo apt-get install postgresql-15 -y',  # Command to execute
            'signal set': 'User created',       # Signal to set
            'signal get': ''                    # Signal to get
        }    
    },
    'suac': {
        'test signal get': {
            'command':'echo $PATH',
            'signal set': '',       # Signal to set
            'signal get': 'User created'         
        }
    }
}

ssh_commands = {    
    'suac': {
        'test signal get': {
            'command':'echo $PATH > /home/u/test',
            'signal set': '',       # Signal to set
            'signal get': ''         
        }
    }
}

apt_install = {
    'susrv': ['postgresql'],
    'g_databases': ['postgresql', 'apache2']
}

pro = VBox()
# pro.apt.remove(apt_install, vms_date, groups)
# pro.execute(vms_date, commands, groups)


scp = {
    'suac': {
        'mode':'push',
        'path_host':'/home/n2ocake/test', # откуда копировать
        'path_vm': '/home/u/'
    }
}


return_scp = {
    'suac': {
        'mode':'pull',
        'path_host':'/home/n2ocake/test2', # откуда копировать
        'path_vm': '/home/u/test'
    }
}

# pro.execute(vms_date, ssh_commands, groups)

# pro.scp.execute(scp, vms_date, groups)
# pro.execute(vms_date, ssh_commands, groups)
# pro.scp.execute(return_scp, vms_date, groups)








_set_hosts.set_hosts(vms_date, "balance.rbt")