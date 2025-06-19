from allta import SystemCommands, VBox, Libvirt, LibvirtManager
from threading import Thread
import requests
import os
import datetime
import re
import pandas as pd
from json import loads
import numpy as np
from ovpn_conf import VM_INFONAME, VM_KERNEL, VM_RESULTS_PATH, VENV_PATH, RANGE, REPORT_PATH, VMS_DATES
from time import sleep
from tasks import scp_provision, task_provision, task_unpack_tar, task_start_server, task_run_iperf, task_add_permission, scp_pull


sys_cls = SystemCommands()

class CreateVM:
    def __init__(self,
                 vbox=None,
                 testdir=None,
                 vm_count=None,
                 vms_dates=VMS_DATES):
        

        self.vbox = vbox
        self.testdir = testdir
        self.vm_count = vm_count
        self.vms = [f"testvm{number}" for number in range(1, self.vm_count + 1)]
        self.vms_dates = vms_dates
        self.main_group = {"main_group": self.vms}
        self.clients_group = {"clients_group": self.vms[1:]}
        self.username = "u"
        self.password = "1"


    def common_build(self):
        Libvirt.prepare()
        self.vms_dates = Libvirt.build(box=self.vbox,
                    rc="1.7.5",
                    vms=self.vms,
                    vms_dates=self.vms_dates)
        return self.vms_dates
    
    
    def provision(self):
        Libvirt.scp(scp_settings=scp_provision,
                    vms_groups=self.main_group,
                    vms_dates=self.vms_dates,
                    username=self.username,
                    password=self.password)
        
        Libvirt.set_hosts(vms_dates=self.vms_dates)
        
        Libvirt.execute(commands=task_provision,
                        vms_groups=self.main_group,
                        vms_dates=self.vms_dates,
                        username=self.username,
                        password=self.password)
        
    
class Test_1(CreateVM):
    def __init__(self,
             vbox=None,
             testdir=None,
             vm_count=None,
             vms_dates=VMS_DATES):
        super().__init__(vbox=vbox, testdir=testdir, vm_count=vm_count, vms_dates=vms_dates)

    # Launch 
    def start(self):
        #Libvirt.execute(commands=task_unpack_tar,
        #                vms_groups=self.main_group,
        #                vms_dates=self.vms_dates,
        #                username=self.username,
        #                password=self.password)
        
        Libvirt.execute(commands=task_start_server,
                        vms_dates=self.vms_dates,
                        username=self.username,
                        password=self.password)
        
        Libvirt.execute(commands=task_run_iperf,
                        vms_groups=self.clients_group,
                        vms_dates=self.vms_dates,
                        username=self.username,
                        password=self.password)
        
        Libvirt.execute(commands=task_add_permission,
                        vms_dates=self.vms_dates,
                        username=self.username,
                        password=self.password)
        
        Libvirt.execute(commands=scp_pull,
                        vms_dates=self.vms_dates,
                        vms_groups=self.clients_group,
                        username=self.username,
                        password=self.password)
    
        
        # vagrant_env = "UPDATE={} BOX_URL={} RC={} KERNEL={} COUNT={} CPU={} RAM={}".format(self.rc_name,
        #                                                                                    self.kernel,
        #                                                                                    self.vm_count,
        #                                                                                    self.vcpu,
        #                                                                                    self.ram)
    
