from time import sleep
from new_balance.roles.vm_info import VERSION_PG, VMS_DATES, VMS_GROUPS, DOMAIN, USERNAME, PASSWORD, PROVIDER, PGOOL_IP
from allta import SystemCommands, Libvirt

class PreConfigure():
    def __init__(self):
        self.provider = PROVIDER

    def apt_install(self):

        

        apt_install = {
            'g_domain_client': ['astra-freeipa-client'],
            'g_database': [f'postgresql-{VERSION_PG}', f'postgresql-{VERSION_PG}-pgpool2'],
            'g_load_balancer': ['pgpool2', 'keepalived', 'postgresql-client', 'sshpass'],
            'dcfreeipa': ['astra-freeipa-server']
        }
        self.provider.apt.install(
            apt_structure=apt_install, vms_dates=VMS_DATES, vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)
        


    def set_hosts(self):
        self.provider.set_hosts(
            domain='balance.rbt', vms_dates=VMS_DATES, username='u', password='1')
        command = f'echo -e "{PGOOL_IP}\tpgpool.{DOMAIN}\tpgpool\n10.177.103.10\tallta.devos.astralinux.ru\tallta\n10.177.43.1\treleases.devos.astralinux.ru\treleases" | tee -a /etc/hosts'
        host_hosts = f'''
sudo cat << 'EOF' >> /etc/hosts
{VMS_DATES['database1']['ip_bridge']}\tdatabase1.{DOMAIN}\tdatabase1
{VMS_DATES['database2']['ip_bridge']}\tdatabase2.{DOMAIN}\tdatabase2
{VMS_DATES['database3']['ip_bridge']}\tdatabase3.{DOMAIN}\tdatabase3
{VMS_DATES['lbdb1']['ip_bridge']}\tlbdb1.{DOMAIN}\tlbdb1
{VMS_DATES['lbdb2']['ip_bridge']}\tlbdb2.{DOMAIN}\tlbdb2
{VMS_DATES['lbdb3']['ip_bridge']}\tlbdb3.{DOMAIN}\tlbdb3
{VMS_DATES['dcfreeipa']['ip_bridge']}\tdcfreeipa.{DOMAIN}\tdcfreeipa
EOF'''
        SystemCommands.cmd(host_hosts)
        SystemCommands.cmd(command)
        hosts = {
            'g_all': {
                'set hosts': {
                    'command': f'sudo sh -c \'{command}\'',
                    'signal set': '',
                    'signal get': ''
                },
            }
        }


        
        self.provider.execute(commands=hosts, vms_dates=VMS_DATES,
                              vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)
        
    def prepare(self):
        if isinstance(self.provider, Libvirt):

            SystemCommands.cmd('sudo qemu-img create -f qcow2 /vms/db1.qcow2 10G && virsh attach-disk database1 /vms/db1.qcow2 vdb --persistent --driver qemu --subdriver qcow2 --targetbus virtio')
            SystemCommands.cmd('sudo qemu-img create -f qcow2 /vms/db2.qcow2 10G && virsh attach-disk database2 /vms/db2.qcow2 vdb --persistent --driver qemu --subdriver qcow2 --targetbus virtio')
            SystemCommands.cmd('sudo qemu-img create -f qcow2 /vms/db3.qcow2 10G && virsh attach-disk database3 /vms/db3.qcow2 vdb --persistent --driver qemu --subdriver qcow2 --targetbus virtio')            

            sleep(10)

            scp = {
                'g_all': [
                    {
                        'mode': 'push',
                        'path_host': '/home/u/git/stress_test/postgresql/new_balance/provision/provision-libvirt.sh',
                        'path_vm': '/tmp/provision-libvirt.sh'
                    }
                ]
            }
            self.provider.scp(scp_settings=scp, vms_dates=VMS_DATES, vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

            execute = {}

            kernel = SystemCommands.check_output_command('uname -r')  
            dns = f"{VMS_DATES['dcfreeipa']['ip_bridge']},192.168.100.1"
            for host in VMS_DATES:
                execute[host] = {
                    'prepare': {
                        'command': f'chmod +x /tmp/provision-libvirt.sh && sudo /tmp/provision-libvirt.sh {host} {kernel} "{dns}"',
                        'signal set': '', 
                        'signal get': ''   
                    },
                }
            
            self.provider.execute(commands=execute, vms_dates=VMS_DATES,
                            vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)     
        self.set_hosts()
        self.apt_install()



            




# virsh -c qemu:///system snapshot-delete --domain database1 --snapshotname build 
# virsh -c qemu:///system destroy --domain database1
# virsh -c qemu:///system undefine --domain database1

# virsh -c qemu:///system snapshot-delete --domain database2 --snapshotname build 
# virsh -c qemu:///system destroy --domain database2
# virsh -c qemu:///system undefine --domain database2

# virsh -c qemu:///system snapshot-delete --domain database3 --snapshotname build 
# virsh -c qemu:///system destroy --domain database3
# virsh -c qemu:///system undefine --domain database3

# virsh -c qemu:///system snapshot-delete --domain lbdb1 --snapshotname build 
# virsh -c qemu:///system destroy --domain lbdb1
# virsh -c qemu:///system undefine --domain lbdb1

# virsh -c qemu:///system snapshot-delete --domain lbdb2 --snapshotname build 
# virsh -c qemu:///system destroy --domain lbdb2
# virsh -c qemu:///system undefine --domain lbdb2

# virsh -c qemu:///system snapshot-delete --domain lbdb3 --snapshotname build 
# virsh -c qemu:///system destroy --domain lbdb3
# virsh -c qemu:///system undefine --domain lbdb3

# virsh -c qemu:///system snapshot-delete --domain dcfreeipa --snapshotname build 
# virsh -c qemu:///system destroy --domain dcfreeipa
# virsh -c qemu:///system undefine --domain dcfreeipa

# rm -rf /vms/* test.log test-box*