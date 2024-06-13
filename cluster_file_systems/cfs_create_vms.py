import os
import requests
from json import loads
from libs.libactions import cmd
from libs.libcfs import check_output_command
from time import sleep

class VMS:
    def __init__(self, rc_vbox=None, vm_count=None, testdir=None, kernel="6.1", hostip=None):
        self.kernel = kernel
        self.rc_name = rc_vbox
        self.vm_count = vm_count
        self.testdir = testdir
        self.hostip = hostip
        self.power_off = 'virsh --connect=qemu:///system destroy {}'
        self.user = 'vagrant'
        self.password = 'vagrant'
        self.check_vm_ip = "virsh --connect=qemu:///system domifaddr {} | awk '{{print $4}}' | tail -n 2"
        self.vms = [f'testvm{number}' for number in range(1, self.vm_count + 1)]

    def prepare_and_start(self):
        astra_config_url = 'http://allta.devos.astralinux.ru/rest/api/get-astra-config'
        response_ac = requests.get(astra_config_url)
        if response_ac.status_code == 200:
            with open('astra-config.json', 'wb') as acb:
                acb.write(response_ac.content)
        else:
            print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

        with open('astra-config.json', 'r') as r:
            dates = loads(r.read())

        def box_wrapper(box):
            true_key = False
            box_name = ''
            box_url = ''
            for i in dates['astra-version']['vagrant_box']:
                if box in str(i):
                    for key in i.keys():
                        if str(key).endswith('o'):
                            true_key = key
                            box_name = i[true_key][0]
                            box_url = i[true_key][1]             
                    
            if true_key == False:
                for i in dates['astra-version']['vagrant_box']:
                    if str(box).startswith('1.7'):
                          if '1.7.1.o' in str(i):
                            box_name = i['1.7.1.o'][0]
                            box_url = i['1.7.1.o'][1]
                    elif str(box).startswith('1.8'):
                        if '1.8.0.o' in str(i):
                            box_name = i['1.8.0.o'][0]
                            box_url = i['1.8.0.o'][1]
            
            return box_name, box_url

        # if not os.path.isdir(self.testdir):
        #     os.mkdir(self.testdir)

        # add_box
        
        box_name, box_url = box_wrapper(self.rc_name)
        print(box_name)
        print(box_url)
        cmd(f'vagrant box add --provider virtualbox {box_name} {box_url}')
        cmd(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')

                # add define pool
        try:
            cmd('virsh --connect=qemu:///system pool-define-as --name default --type dir --target /var/lib/libvirt/images')
            cmd('virsh --connect=qemu:///system pool-autostart default')
            cmd('virsh --connect=qemu:///system pool-start default')
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')
        
         # create_vm
        print(f'UPDATE={box_name} HOSTIP={self.hostip} BOX_URL={box_url} RC={self.rc_name} KERNEL={self.kernel} COUNT={self.vm_count} vagrant up --provider=libvirt')
        cmd(f'UPDATE={box_name} HOSTIP={self.hostip} BOX_URL={box_url} RC={self.rc_name} KERNEL={self.kernel} COUNT={self.vm_count} vagrant up --provider=libvirt')
        
        print('\nWait reboot VMs 180s...\n')
        sleep(180)

        self.vm_dates = {
             vm:{
                'ip': check_output_command(self.check_vm_ip.format(vm)).split('/')[0],
                'login':f'{self.user}',
                'password':f'{self.password}'
                } for vm in self.vms}
        print(f'VM dates is:\n{self.vm_dates}')


if __name__ == "__main__":
    vm1 = VMS(rc_vbox="1.8.0.14", vm_count=3, hostip="10.177.103.202")
    vm1.prepare_and_start()
    tmp = vm1.vm_dates
    print("VM DATES\n", tmp)
