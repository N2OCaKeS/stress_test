from allta import VBoxManager

from roles.vm_info import VMS_DATES, VMS_GROUPS, USERNAME, PASSWORD


class keepalived:
    def keepalived():

        lbdb1 = """sudo tee /etc/keepalived/keepalived.conf > /dev/null <<EOF
vrrp_instance VI_1 {
    state MASTER
    interface eth0
    virtual_router_id 51
    priority 150
    advert_int 1
    authentication {
        auth_type PASS
        auth_pass securepass
    }
    virtual_ipaddress {
        10.177.103.131
    }
}
EOF"""

        lbdb2 = """sudo tee /etc/keepalived/keepalived.conf > /dev/null <<EOF
vrrp_instance VI_1 {
    state BACKUP
    interface eth0
    virtual_router_id 51
    priority 100
    advert_int 1
    authentication {
        auth_type PASS
        auth_pass securepass
    }
    virtual_ipaddress {
        10.177.103.131
    }
}
EOF"""

        lbdb3 = """sudo tee /etc/keepalived/keepalived.conf > /dev/null <<EOF
vrrp_instance VI_1 {
    state BACKUP
    interface eth0
    virtual_router_id 51
    priority 90
    advert_int 1
    authentication {
        auth_type PASS
        auth_pass securepass
    }
    virtual_ipaddress {
        10.177.103.131
    }
}
EOF"""

        configure_keepalived = {
            'g_load_balancer': {
                'create keepalived conf file': {
                    'command': f'sudo touch /etc/keepalived/keepalived.conf',
                    'signal set': 'create file',
                    'signal get': ''
                }
            },

            'lbdb1': {
                'configure keepalived': {
                    'command': lbdb1,
                    'signal set': '',
                    'signal get': ['create file']
                }
            },
            'lbdb2': {
                'configure keepalived': {
                    'command': lbdb2,
                    'signal set': '',
                    'signal get': ['create file']
                }
            },
            'lbdb3': {
                'configure keepalived': {
                    'command': lbdb3,
                    'signal set': '',
                    'signal get': ['create file']
                }
            },
        }

        VBoxManager.execute(commands=configure_keepalived, vms_dates=VMS_DATES,
                            vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

        start_keepalived = {
            'g_load_balancer': {
                'sysctl conf': {
                    'command': 'echo "net.ipv4.ip_nonlocal_bind=1" | sudo tee -a /etc/sysctl.conf && sudo sysctl -p',
                    'signal set': 'sysctl conf',
                    'signal get': ''
                },
                'start keepalived': {
                    'command': 'sudo systemctl enable keepalived && sudo systemctl restart keepalived',
                    'signal set': 'keepalived start',
                    'signal get': ['sysctl conf']
                },
            }
        }

        VBoxManager.execute(commands=start_keepalived, vms_dates=VMS_DATES,
                            vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)
