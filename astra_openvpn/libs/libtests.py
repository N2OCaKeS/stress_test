from allta import SystemCommands, VBox
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
from ovpn_conf import VM_INFONAME, VM_KERNEL, VM_RESULTS_PATH, VENV_PATH, RANGE
from time import sleep


sys_cls = SystemCommands()

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
        sys_cls.cmd(f'vagrant box add --provider virtualbox {box_name} {box_url}')
        print(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')
        sys_cls.cmd(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')

        # add define pool
        try:
            sys_cls.cmd('virsh pool-define-as --name default --type dir --target /var/lib/libvirt/images')
            sys_cls.cmd('virsh pool-autostart default')
            sys_cls.cmd('virsh pool-start default')
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')
        
        vagrant_env = "UPDATE={} BOX_URL={} RC={} KERNEL={} COUNT={} CPU={} RAM={}".format(box_name,
                                                                                           box_url,
                                                                                           self.rc_name,
                                                                                           self.kernel,
                                                                                           self.vm_count,
                                                                                           self.vcpu,
                                                                                           self.ram)
        # create_vm
        print(f"{vagrant_env} vagrant up --provide=libvirt")
        sys_cls.cmd(f'{vagrant_env} vagrant up --provider=libvirt')

        print('\nWait reboot VMs 100s...\n')
        sleep(100)
        
        #sys_cls.cmd(f"{vagrant_env} vagrant upload provision/vpn_provision.sh /home/vagrant/ testvm1")
        #sys_cls.cmd(f'{vagrant_env} vagrant ssh testvm1 -c "sudo bash /home/vagrant/vpn_provision.sh"')

        # interfaces + ext provision
        # vpn machine
        #sys_cls.cmd("virsh attach-interface testvm1 --source vpn-net --type network --model virtio --config --persistent")
        #sys_cls.cmd("virsh reboot testvm1")
        #sys_cls.cmd(f"{vagrant_env} vagrant upload provision/vpn_provision.sh /home/vagrant/ testvm1")
        #sys_cls.cmd(f'{vagrant_env} vagrant ssh testvm1 -c "sudo bash /home/vagrant/vpn_provision.sh"')
        # pooler machine
        #sys_cls.cmd("virsh attach-interface testvm2 --source pooler-net --type network --model virtio --config --persistent")
        #sys_cls.cmd("virsh reboot testvm2")
        #sys_cls.cmd(f"{vagrant_env} vagrant upload provision/pooler_provision.sh /home/vagrant/ testvm2")
        #sys_cls.cmd(f'{vagrant_env} vagrant ssh testvm2 -c "sudo bash /home/vagrant/pooler_provision.sh"')



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
        self.ranger = RANGE


    def start_test(self):
        self.vms_dates = {
             vm:{
                "host-port": 22,
                'ip_bridge': sys_cls.check_output_command(self.check_vm_ip.format(vm)).split('/')[0], 
                'login':f'{self.user}',
                'password':f'{self.password}'
                } for vm in self.vms}
        self.domain = "stress.rbt"
        
        # Daemon Creater
        con_generator = {"testvm2": {}}
        for i in range(0, self.ranger):
            con_generator["testvm2"].update({ 
                    f"create_daemon_{i}": {
                        "command": (
                            "echo '[Unit]\n"
                            "Description=OpenVPN Client\n"
                            "After=network.target\n\n"
                            "[Service]\n"
                            f"WorkingDirectory=/home/vagrant/clients_keys/tester{i}\n"
                            "ExecStart=/usr/sbin/openvpn --config client.ovpn\n"
                            "Restart=always\n"
                            "User=root\n\n"
                            "[Install]\n"
                            f"WantedBy=multi-user.target' | sudo tee /etc/systemd/system/openvpn-client-tester{i}.service"
                        ),
                        "signal set": f"{i}",
                        "signal get": [f"{i + 1 if i != 0 else ""}"]
                    },
                    f"enable_service_{i}": {
                        "command": (
                            f"sudo systemctl daemon-reload && sudo systemctl enable --now openvpn-client-tester{i}"),
                        "signal set": f"",
                        "signal get": [f"{i}"]
                    }
                })
   

        # scp client_keys - last
        scp_configs = {
            "testvm2": {
                "copy_keys": {
                    "command": (
                        f"nohup sshpass -p {self.vms_dates['testvm1']['password']} scp -o StrictHostKeyChecking=no -r "
                        f"{self.vms_dates['testvm1']['login']}@{self.vms_dates['testvm1']['ip_bridge']}"
                        ":/etc/openvpn/clients_keys/ /home/vagrant/ > scp.log 2>&1"
                    ),
                    "signal set": "",
                    "signal get": ""
                }}}
        

        commands_scp = {
            "testvm2": {
                "mode": "push",
                "path_host": "./vpn_perf.py",
                "path_vm": "/home/vagrant/"
            }
        }


        print(self.vms_dates["testvm1"]["ip_bridge"])
        # # Создание VRF
        # for j in range(1, VRF_COUNT + 1):
        #     try:
        #         if j % 10 == 0:
        #             print(f"Запущено в VRF {j} клиентов.")
        #         vrf_name = f"vrf{j}"
        #         table_id = 1000 + j
        #         sys_cls.cmd(f"ip link add {vrf_name} type vrf table {table_id}")
        #         sys_cls.cmd(f"ip link set dev {vrf_name} up")
        #         print(2)
        #         for i in range(1, RANGE + 1):
        #             tun_dev = f"tun{i}"
        #             cfg_file = f"/home/vagrant/clients_keys/tester{i}/client.ovpn"
        #             ip = self.vms_dates["testvm1"]["ip_bridge"]

        #             sys_cls.cmd(f"sed -i 's/^remote 192\\.168\\.121\\.35 1194$/remote {ip} 1194/' /home/vagrant/clients_keys/tester{i}/client.ovpn")
        #             sys_cls.cmd(f"openvpn --config {cfg_file} --dev {tun_dev} --daemon")
        #             sys_cls.cmd(f"ip link set dev {tun_dev} master {vrf_name}")
        #             sys_cls.cmd(f"ip link set dev {tun_dev} up")
        #             sys_cls.cmd(f"ip route add 10.8.0.1 dev {tun_dev} vrf {vrf_name} ")
        #             if PER_VRF - i == 0:
        #                 break

        #     except Exception as e:
        #         print("Ошибка", e)

        # print(f"Создано {VRF_COUNT} таблиц.\nНа каждую таблицу - {PER_VRF} туннелей.")


        #VBox.set_hosts(domain=self.domain,
        #               vms_dates=self.vms_dates)
        
        # 2. Отправляем клиентам
        #VBox.execute(commands=scp_configs, 
        #             vms_dates=self.vms_dates, 
        #             username=self.user, 
        #             password=self.password)
        
        # 3. Создаем демонов
        #VBox.execute(commands=con_generator,
        #             vms_dates=self.vms_dates,
        #             username=self.user,
        #             password=self.password)

        # 4. Script внутри
        #VBox.scp(scp_settings=commands_scp,
        #         vms_dates=self.vms_dates,
        #         username=self.user,
        #         password=self.password)
        
        print(f'VM dates is:\n{self.vms_dates}')
        
        


        
        

    def vms_destroy(self):
        try:
            [
                sys_cls.cmd(f'virsh dumpxml {vm_name}') for vm_name in self.vms
            ]
            [
                sys_cls.cmd(self.destroy.format(vm_name)) for vm_name in self.vms
            ]
            [
                sys_cls.cmd(self.undefine.format(vm_name)) for vm_name in self.vms
            ]
            sys_cls.cmd('rm -rf .vagrant')
        except Exception as e:
            print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

