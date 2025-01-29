import os
import requests
from json import loads
from time import sleep

from libs.libs import cmd, check_output_command

class ManageVM:
    def __init__(self,
                 rc_vbox=None,
                 testdir=None,
                 vm_count=None,
                 kernel=None,
                 vcpu=None,
                 ram=None,
                 mode='o'):
        
        """
        :param rc_vbox: Параметр rc_vbox, значение по умолчанию None.
        :param testdir: Параметр testdir, значение по умолчанию None.
        :param vm_count: Количество виртуальных машин, значение по умолчанию None.
        :param kernel: Параметр kernel, значение по умолчанию None.
        :param vcpu: Количество виртуальных процессоров, значение по умолчанию None.
        :param ram: Объём оперативной памяти, значение по умолчанию None.
        :param mode: Режим, значение по умолчанию o.
        """

        self.mode = mode
        self.rc_name = rc_vbox
        self.testdir = testdir
        self.kernel = kernel
        self.vm_count = vm_count
        self.vcpu = vcpu
        self.ram = ram
        ####
        self.power_off = 'virsh --connect=qemu:///system destroy {}'
        self.user = 'vagrant'
        self.password = 'vagrant'
        self.check_vm_ip = "virsh --connect=qemu:///system domifaddr {} | awk '{{print $4}}' | tail -n 2"
        self.vms = [f'testvm{number}' for number in range(1, self.vm_count + 1)]

    def prepare_and_start_vm(self):
        astra_config_url = 'http://allta.devos.astralinux.ru/rest/api/get-box-config'
        response_ac = requests.get(astra_config_url)
        if response_ac.status_code == 200:
            with open('box-config.json', 'wb') as acb:
                acb.write(response_ac.content)
        else:
            print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

        with open('box-config.json', 'r') as r:
            dates = loads(r.read())

        def __box_wrapper(box, mode):
            true_key = False
            box_name = ''
            box_url = ''
            for i in dates['vagrant_box']:
                if box in str(i):
                    for key in i.keys():
                        if str(key).endswith(mode):
                            true_key = key
                            box_name = i[true_key][0]
                            box_url = i[true_key][1]             
                    
            if true_key == False:
                for i in dates['vagrant_box']:
                    if str(box).startswith('1.7'):
                          if f'1.7.1.{mode}' in str(i):
                            box_name = i[f'1.7.1.{mode}'][0]
                            box_url = i[f'1.7.1.{mode}'][1]
                    elif str(box).startswith('1.8'):
                        if f'1.8.0.{mode}' in str(i):
                            box_name = i[f'1.8.0.{mode}'][0]
                            box_url = i[f'1.8.0.{mode}'][1]
            
            return box_name, box_url

        # if not os.path.isdir(self.testdir):
        #     os.mkdir(self.testdir)

        # add_box
        self.box_name, self.box_url = __box_wrapper(self.rc_name, self.mode)
        print(f'vagrant box add --provider virtualbox {self.box_name} {self.box_url}')
        cmd(f'vagrant box add --provider virtualbox {self.box_name} {self.box_url}')
        print(f'vagrant mutate {self.box_name} libvirt --input-provider virtualbox --force-virtio')
        cmd(f'vagrant mutate {self.box_name} libvirt --input-provider virtualbox --force-virtio')

        # add define pool
        try:
            cmd('virsh pool-define-as --name default --type dir --target /var/lib/libvirt/images')
            cmd('virsh pool-autostart default')
            cmd('virsh pool-start default')
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

        # create_vm
    
   
        print('UPDATE={} BOX_URL={} RC={} KERNEL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(self.box_name,
                                                                                                                self.box_url,
                                                                                                                self.rc_name,
                                                                                                                self.kernel,
                                                                                                                self.vm_count,
                                                                                                                self.vcpu,
                                                                                                                self.ram))
        cmd('UPDATE={} BOX_URL={} RC={} KERNEL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(self.box_name,
                                                                                                            self.box_url,
                                                                                                            self.rc_name,
                                                                                                            self.kernel,
                                                                                                            self.vm_count,
                                                                                                            self.vcpu,
                                                                                                            self.ram))

        print('\nWait reboot VMs 180s...\n')
        sleep(180)

        self.vm_dates = {
            vm: {
            'ip': check_output_command(self.check_vm_ip.format(vm)).split('/')[0],
            'login':f'{self.user}',
            'password':f'{self.password}'
            } for vm in self.vms
        }
        # self.vm_dates = {
        #     'ip': check_output_command(self.check_vm_ip.format("testvm1")).split('/')[0],
        #     'login':f'{self.user}',
        #     'password':f'{self.password}'
        # }

        
    def destroy_vm(self):
        # pass
        cmd('UPDATE={} BOX_URL={} RC={} KERNEL={} COUNT={} CPU={} RAM={} vagrant destroy --force'.format(self.box_name,
                                                                                                         self.box_url,
                                                                                                         self.rc_name,
                                                                                                         self.kernel,
                                                                                                         self.vm_count,
                                                                                                         self.vcpu,
                                                                                                         self.ram))
        # print(f'VM dates is:\n{self.vm_dates}')


if __name__ == "__main__":
    from conf import vCPU, RAM
    vm  = ManageVM(rc_vbox="1.8.1",
                #testdir=...,
                vm_count=3,
                kernel="6.1",
                vcpu=vCPU,
                ram=RAM)

    vm.prepare_and_start_vm()
    data_vm = vm.vm_dates
    print(data_vm)
