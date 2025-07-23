from allta import SystemCommands, Libvirt
import json
from ovpn_conf import VM_INFONAME, VM_KERNEL, VM_RESULTS_PATH, VENV_PATH, RANGE, REPORT_PATH, VMS_DATES, VMS
from tasks import test_1


sys_cls = SystemCommands()

class CreateVM:
    def __init__(self,
                 vbox=None,
                 vm_count=None,
                 rc_name=None,
                 vms_dates=VMS_DATES,
                 vms=VMS,
                 provider=Libvirt()):
        
        self.provider = provider
        self.vbox = vbox
        self.vm_count = vm_count
        self.vms = vms
        self.vms_dates = vms_dates
        self.rc_name = rc_name
        self.main_group = {"main_group": self.vms}
        self.clients_group = {"clients_group": self.vms[1:]}
        self.username = "u"
        self.password = "1"

    
    def common_build(self):
        self.provider.prepare()
        self.vms_dates = self.provider.build(box=self.vbox,
                                             rc=self.rc_name,
                                             vms=self.vms,
                                             vms_dates=self.vms_dates)
        return self.vms_dates
    
    
    def provision(self):
        self.provider.scp(scp_settings=test_1["scp_provision"],
                    vms_groups=self.main_group,
                    vms_dates=self.vms_dates,
                    username=self.username,
                    password=self.password)
        
        # vms_dates write
        print(self.vms_dates)
        with open("vms_dates.txt", "w", encoding="UTF-8") as f:
            json.dump(self.vms_dates, f)

        self.provider.set_hosts(vms_dates=self.vms_dates,
                          domain="stress.rbt")
        
        self.provider.execute(commands=test_1["task_provision"],
                        vms_groups=self.main_group,
                        vms_dates=self.vms_dates,
                        username=self.username,
                        password=self.password)
        
    
class Test_1(CreateVM):
    def __init__(self,
             vbox=None,
             vm_count=None,
             rc_name=None,
             vms_dates=VMS_DATES):
        super().__init__(vbox=vbox, rc_name=rc_name, vm_count=vm_count, vms_dates=vms_dates)

    # Launch 
    def start(self):

        # vms_dates read
        with open("vms_dates.txt", "r", encoding="UTF-8") as f:
            json_string = f.read()
            self.vms_dates = json.loads(json_string)

        self.provider.execute(commands=test_1["task_unpack_tar"],
                         vms_groups=self.main_group,
                         vms_dates=self.vms_dates,
                         username=self.username,
                         password=self.password)
    
        self.provider.sed(sed_conf=test_1["task_sed_cipher"],
                          vms_dates=self.vms_dates,
                          username=self.username,
                          password=self.password)

        self.provider.execute(commands=test_1["task_run_server"],
                         vms_dates=self.vms_dates,
                         username=self.username,
                         password=self.password)

        self.provider.execute(commands=test_1["task_run_clients"],
                         vms_groups=self.clients_group,
                         vms_dates=self.vms_dates,
                         username=self.username,
                         password=self.password)
        
        self.provider.execute(commands=test_1["task_add_permission"],
                         vms_dates=self.vms_dates,
                         vms_groups=self.clients_group,
                         username=self.username,
                         password=self.password)
        
        self.provider.scp(scp_settings=test_1["scp_pull"],
                    vms_dates=self.vms_dates,
                    vms_groups=self.clients_group,
                    username=self.username,
                    password=self.password)
