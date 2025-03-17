from vbox import VBox
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
        'ip_bridge':'127.0.0.1',
        'cpus':'4',
        'memory':'4096'
    },
}

groups = {
    'databases': ['suac', 'susrv']
}

commands = {
    'g_databases': { # имя хоста или имя группы хостов на которых нужно выполнить команду имя группы будет называться с g_ в начале
        'test signal set': { # имя задачи
            'command':'sudo apt install postgresql -y',  # Command to execute
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


apt_install = {
    'susrv': ['postgresql'],
    'g_databases': ['postgresql', 'apache2']
}

pro = VBox()
pro.apt.remove(apt_install, vms_date, groups)
# pro.execute(vms_date, commands, groups)






