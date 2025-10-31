import os
import requests
from json import loads
from libs.libactions import cmd
from libs.libcfs import check_output_command
from time import sleep
from allta import Libvirt, LibvirtManager


class VMS:
    def __init__(self, rc_vbox=None, vm_count=None, testdir=None, kernel="6.1", hostip=None, mode='o', provider=Libvirt(), parsec=False):
        self.kernel = kernel
        self.rc_name = rc_vbox
        self.vm_count = vm_count
        self.testdir = testdir
        self.hostip = hostip
        self.power_off = 'virsh --connect=qemu:///system destroy {}'
        self.user = 'u'
        self.password = '1'
        self.check_vm_ip = "virsh --connect=qemu:///system domifaddr {} | awk '{{print $4}}' | tail -n 2"
        self.vms = [f'testvm{number}' for number in range(1, self.vm_count + 1)]
        self.mode = mode
        self.provider = provider
        self.parsec = parsec

    def prepare_and_start(self):
        VERSION_OS = '.'.join(self.rc_name.split('.')[:2])

        VMS = [f'testvm{i}' for i in range(1, int(self.vm_count) + 1)]  # Краткий список ВМ

        VMS_DATES = { # Полный список ВМ
            testvm: {'host-port': '22',
                     'cpu': '4',
                     'ram': '8192',
                     'disk': '15'}
            for testvm in VMS
        }

        if self.parsec:
            security_mode = 's'
        else:
            security_mode = 'o'
        if isinstance(self.provider, Libvirt):
            self.provider.prepare()
            if VERSION_OS == '1.7':
                VMS_DATES = self.provider.build(f"1.7.5.{security_mode}", self.rc_name, VMS, VMS_DATES)
            elif VERSION_OS == '1.8':
                VMS_DATES = self.provider.build(f"1.8.1.{security_mode}", self.rc_name, VMS, VMS_DATES)

        self.provider.check(VMS, VMS_DATES)
        print(f'VMS DATES:\n{VMS_DATES}')

        cmd("sudo firewall-cmd --permanent --zone=libvirt --add-service=nfs")
        cmd("sudo firewall-cmd --permanent --zone=libvirt --add-service=mountd")
        cmd("sudo firewall-cmd --permanent --zone=libvirt --add-service=rpc-bind")
        cmd("sudo firewall-cmd --reload")
        sleep(30)
        
        self.provider.scp(
            scp_settings={
                'g_VMS': [
                    {
                        'mode': 'push', 
                        'path_host': '/home/u/git/stress_test/cluster_file_systems/req.txt', 
                        'path_vm': '/home/u/req.txt'
                    }
                ]
            },
            vms_dates=VMS_DATES,
            vms_groups={
                'VMS':VMS
            }
        )
        print(f'<{str(self.provider.scp.__name__).upper()}> block done ' + ('*' * 50))

        self.provider.scp(
            scp_settings={
                'g_VMS': [
                    {
                        'mode': 'push', 
                        'path_host': '/home/u/git/stress_test/cluster_file_systems/provision.sh', 
                        'path_vm': '/home/u/env_provision.sh'
                    }
                ]
            },
            vms_dates=VMS_DATES,
            vms_groups={
                'VMS':VMS
            }
        )
        print(f'<{str(self.provider.scp.__name__).upper()}> block done ' + ('*' * 50))

        self.provider.execute(
            commands={
                'g_VMS':{
                    'provision':{
                        'command':f"sudo bash /home/u/env_provision.sh {self.rc_name} {self.kernel}",
                        'signal set': 'provision', 
                        'signal get': ''
                    },
                    'reboot':{ 
                        'signal set': '', 
                        'signal get': ['provision']
                    }
                }
            },
            vms_dates=VMS_DATES,
            vms_groups={
                'VMS':VMS
            }
        )
        print(f'<{str(self.provider.execute.__name__).upper()}> block done ' + ('*' * 50))

        self.vm_dates = {
             vm:{
                'ip': check_output_command(self.check_vm_ip.format(vm)).split('/')[0],
                'login':f'{self.user}',
                'password':f'{self.password}'
                } for vm in self.vms}
        print(f'VM dates is:\n{self.vm_dates}')

        LibvirtManager.Vm.stop(self.vm_dates.keys())



if __name__ == "__main__":
    vm1 = VMS(rc_vbox="1.8.3.4", vm_count=3, hostip="10.177.103.203")
    vm1.prepare_and_start()
    tmp = vm1.vm_dates
    print("VM DATES\n", tmp)