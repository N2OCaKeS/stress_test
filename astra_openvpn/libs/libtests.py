from allta import SystemCommands
from libs.ovpnlib import (check_output_command, 
                          cmd, 
                          send_remote_command,
                          create_remote_file,
                          get_remote_file)
from threading import Thread
import requests
import os
import datetime
import re
import pandas as pd
from json import loads
import numpy as np
from ovpn_conf import VM_INFONAME, VM_KERNEL, VM_RESULTS_PATH
from time import sleep


sys_com = SystemCommands()


class CreateVM:
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
        :param mode: Режим, значение по умолчанию None.
        """

        self.mode = mode
        self.rc_name = rc_vbox
        self.testdir = testdir
        self.kernel = kernel
        self.vm_count = vm_count
        self.vcpu = vcpu
        self.ram = ram

    def prepare_vms(self):
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

        if not os.path.isdir(self.testdir):
            os.mkdir(self.testdir)

        # add_box
        box_name, box_url = __box_wrapper(self.rc_name, self.mode)
        print(f'vagrant box add --provider virtualbox {box_name} {box_url}')
        sys_com.cmd(f'vagrant box add --provider virtualbox {box_name} {box_url}')
        print(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')
        sys_com.cmd(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')

        # add define pool
        try:
            sys_com.cmd('virsh pool-define-as --name default --type dir --target /var/lib/libvirt/images')
            sys_com.cmd('virsh pool-autostart default')
            sys_com.cmd('virsh pool-start default')
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

        # create_vm
        print('UPDATE={} BOX_URL={} RC={} KERNEL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(box_name,
                                                                                                                 box_url,
                                                                                                                 self.rc_name,
                                                                                                                 self.kernel,
                                                                                                                 self.vm_count,
                                                                                                                 self.vcpu,
                                                                                                                 self.ram))
        sys_com.cmd('UPDATE={} BOX_URL={} RC={} KERNEL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(box_name,
                                                                                                               box_url,
                                                                                                               self.rc_name,
                                                                                                               self.kernel,
                                                                                                               self.vm_count,
                                                                                                               self.vcpu,
                                                                                                               self.ram))

        print('\nWait reboot VMs 100s...\n')
        sleep(100)


class Ovpn20k(CreateVM):
    def __init__(self, 
                 rc_vbox=None, 
                 testdir=None, 
                 vm_count=None, 
                 kernel=None, 
                 vcpu=None, 
                 ram=None):
        super().__init__(rc_vbox, testdir, vm_count, kernel, vcpu, ram)

        self.vms = [f'testvm{number}' for number in range(1, self.vm_count + 1)]
        self.user = 'vagrant'
        self.password = 'vagrant'
        self.check_vm_ip = "virsh domifaddr {} | awk '{{print $4}}' | tail -n 2"
        self.vg_destroy = 'vagrant destroy {}'
        self.destroy = 'virsh destroy {}'
        self.undefine = 'virsh undefine {}'


    def start_test(self):
        self.vm_dates = {
             vm:{
                'ip': sys_com.check_output_command(self.check_vm_ip.format(vm)).split('/')[0],
                'login':f'{self.user}',
                'password':f'{self.password}'
                } for vm in self.vms}
        print(f'VM dates is:\n{self.vm_dates}')


    def vms_destroy(self):
        try:
            [
                sys_com.cmd(f'virsh dumpxml {vm_name}') for vm_name in self.vms
            ]
            [
                sys_com.cmd(self.destroy.format(vm_name)) for vm_name in self.vms
            ]
            [
                sys_com.cmd(self.undefine.format(vm_name)) for vm_name in self.vms
            ]
            sys_com.cmd('rm -rf .vagrant')
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')
