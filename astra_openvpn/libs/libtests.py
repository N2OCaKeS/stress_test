from allta import SystemCommands, VBox, Libvirt, LibvirtManager
from threading import Thread
import requests
import os
import datetime
import re
import pandas as pd
import json
import numpy as np
from ovpn_conf import VM_INFONAME, VM_KERNEL, VM_RESULTS_PATH, VENV_PATH, RANGE, REPORT_PATH, VMS_DATES, VMS
from time import sleep
from tasks import test_1, scp_pull


sys_cls = SystemCommands()

class CreateVM:
    def __init__(self,
                 vbox=None,
                 testdir=None,
                 vm_count=None,
                 vms_dates=VMS_DATES,
                 vms=VMS):
        

        self.vbox = vbox
        self.testdir = testdir
        self.vm_count = vm_count
        self.vms = vms
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
        Libvirt.scp(scp_settings=test_1["scp_provision"],
                    vms_groups=self.main_group,
                    vms_dates=self.vms_dates,
                    username=self.username,
                    password=self.password)
        
        # vms_dates write
        print(self.vms_dates)
        with open("vms_dates.txt", "w", encoding="UTF-8") as f:
            json.dump(self.vms_dates, f)

        Libvirt.set_hosts(vms_dates=self.vms_dates,
                          domain="stress.rbt")
        
        Libvirt.execute(commands=test_1["task_provision"],
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

        # vms_dates read
        with open("vms_dates.txt", "r", encoding="UTF-8") as f:
            json_string = f.read()
            self.vms_dates = json.loads(json_string)

        Libvirt.execute(commands=test_1["task_unpack_tar"],
                         vms_groups=self.main_group,
                         vms_dates=self.vms_dates,
                         username=self.username,
                         password=self.password)
        
        Libvirt.execute(commands=test_1["task_start_server"],
                         vms_dates=self.vms_dates,
                         username=self.username,
                         password=self.password)
        
        Libvirt.execute(commands=test_1["task_run_iperf"],
                         vms_groups=self.clients_group,
                         vms_dates=self.vms_dates,
                         username=self.username,
                         password=self.password)
        
        Libvirt.execute(commands=test_1["task_add_permission"],
                         vms_dates=self.vms_dates,
                         username=self.username,
                         password=self.password)
        
        Libvirt.scp(scp_settings=test_1["scp_pull"],
                    vms_dates=self.vms_dates,
                    vms_groups=self.clients_group,
                    username=self.username,
                    password=self.password)
