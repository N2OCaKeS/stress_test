# Start Test_1:

from ovpn_conf import VMS_DATES

# Provision ->

scp_provision = {
    "g_main_group": [
        {
            'mode': 'push',
            'path_host': '/home/u/git/stress_test/astra_openvpn',
            'path_vm': '/home/u/'
        }
    ]
}

task_provision = {
    "g_main_group": {
        "task_provision": {
            "command": (
                'sudo su -c "bash /home/u/astra_openvpn/provision/env_provision.sh"'
            )
        }
    }
}

# Launch ->

task_unpack_tar = {
    "g_main_group": {
        "unpack": {
            "command": (
                f"cd /home/u/ && tar -xzvf ovpn.tar.gz > /dev/null 2>&1 && "
                'sudo su -c "cp -r /home/u/openvpn /etc/"'
            ),
            "signal set": "",
            "signal get": ""
        },
    }
}

task_start_server = {
    "testvm1": {
        "start_server":
        {
            "command": (
            'sudo su root -c "astra-openvpn-server start" && '
            'sudo su root -c "iperf -s -u -B 10.8.0.1 -i 1 -D "'
        ),
        "signal set": "",
        "signal get": ""
        }
    }
}

task_run_iperf = {
    'g_clients_group': {
        "run_perf": {
            "command": (
                'sudo su -c "ulimit -u 100000 && '
                'ulimit -n 100000 && '
                'ulimit -s 100000 && '
                '/home/u/python/Python-3.12.1/venv/bin/python /home/u/astra_openvpn/vpn_perf.py"'
            ),
            "signal set": "",
            "signal get": ""
        }
    }
}

task_add_permission = {
    "testvm1": {
        "add_permission":{
            "command":
                'sudo su -c "chmod -R 777 /var/log/openvpn && chown -R u:u /var/log/openvpn"',
            "signal set": "",
            "signal get": ""
        }
    }
}

# Pull results ->

scp_pull = {
    "testvm1": {
        "mode": "pull",
        "path_host": "./results/raw_results",
        "path_vm": "/var/log/openvpn/"
    },
    "g_clients_group":[
        
        {
            "mode": "pull",
            "path_host": f"./results/raw_results/iperf_{vm}/",
            "path_vm": "/var/log/iperf/"
        }
        for vm in [key for key in VMS_DATES][1:]
    ]
}

# End Test_1.
