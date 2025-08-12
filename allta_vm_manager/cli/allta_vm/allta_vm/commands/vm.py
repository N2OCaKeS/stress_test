from allta import Libvirt, LibvirtManager, SystemCommands
from allta_vm.commands.snapshot import Snapshot
from allta_vm.config import get_repo, load_vms_dates, edit_vm
from time import sleep

class Vm:
    def create(info_path, box: str = "vm_station", rc: str = None, kernel: str = None):
        lv = Libvirt()
        vms_dates = load_vms_dates(info_path=info_path)
        vms_list: list = list(vms_dates.keys())
        new_vms_dates = lv.build(box=box, rc=rc, vms=vms_list, vms_dates=vms_dates, kernel=kernel)
        group = {'all':vms_list}
        rc_list = ["1.7.5.9", "1.8.1.6"]

        for rc in rc_list:
            LibvirtManager.Snapshot.revert(vms=vms_list, snapshot_name=f"{rc}_build")
            SystemCommands.cmd("sudo rm -rf /root/.ssh/known_hosts ~/.ssh/known_hosts")
            scp_prepare = {
                "g_all": [
                    {
                        'mode': 'push',
                        'path_host': '/opt/allta_vm/vm/provision.sh',
                        'path_vm': '/home/u/env_provision.sh'            
                    }
                ],        
            }
            Libvirt.scp(scp_settings=scp_prepare, vms_dates=new_vms_dates, vms_groups=group, username='u', password='1')

            prepare = {}

            for vm_name in vms_dates:
                prepare[vm_name] = {
                    'set hostname': {
                        'command': f"sudo hostnamectl set-hostname {vm_name} && if grep -q '^127\\.0\\.1\\.1' /etc/hosts; then sudo sed -i 's/^127\\.0\\.1\\.1.*/127.0.1.1\\t{vm_name}/' /etc/hosts; else echo -e '127.0.1.1\\t{vm_name}' | sudo tee -a /etc/hosts; fi",
                    'signal set': 'hostname',
                        'signal get': ''
                    },
                    'prepare': {
                        'command': f"sudo chmod 777 /home/u/env_provision.sh && sudo su -c '/home/u/env_provision.sh {vms_dates[vm_name]["ip_bridge"]}'",
                        'signal set': 'prepare',
                        'signal get': ['hostname']
                    },
                    'confirm': {
                        'command': f"(sleep 2 && sudo reboot) &",
                        'signal set': '',
                        'signal get': ['prepare']
                    },        
                }

            Libvirt.execute(commands=prepare, vms_dates=new_vms_dates, vms_groups=group, username='u', password='1')
            sleep(20)
            for vm in vms_list:
                SystemCommands.cmd_with_returncode(f"/opt/allta_vm/vm/network.sh {vm}")            
            LibvirtManager.Snapshot.create(vms=vms_list, snapshot_name=rc)  

            SystemCommands.cmd("")
            LibvirtManager.Snapshot.delete(vms=vms_list, snapshot_name=f"{rc}_build")

    def base_create():
        Vm.create("/opt/allta_vm/vm/base_vm.json")


    def delete(vms: list):
        Snapshot.delete_all(vms=vms)
        for vm in vms:
            SystemCommands.cmd_with_returncode(f"sudo virsh -c qemu:///system destroy --domain {vm}")
            SystemCommands.cmd_with_returncode(f"sudo virsh -c qemu:///system undefine --remove-all-storage --delete-storage-volume-snapshots --domain {vm}")

    def update(info_path:str):
        vms_dates = load_vms_dates(info_path=info_path)
        vms = list(vms_dates.keys())  
        LibvirtManager.Vm.stop(vms=vms)        
        for vm in vms:
            xml_file = f"/vms/{vm}.xml"
            SystemCommands.check_output_command(f"sudo virsh -c qemu:///system dumpxml --domain {vm} > {xml_file}")
            new_file = edit_vm(xml_path=xml_file, cpu=vms_dates[vm]['cpu'], ram_mb=vms_dates[vm]['ram'])
            SystemCommands.check_output_command(f"virsh -c qemu:///system define {new_file}")
        LibvirtManager.Vm.start(vms=vms)

    def astra_update(info_path: str, rc: str):
        repo = get_repo(rc=rc)
        vms_dates = load_vms_dates(info_path=info_path)
        vms = list(vms_dates.keys())
        task = {}    
        vms = list(vms_dates.keys())
        if rc.startswith("1.7"):
            LibvirtManager.Snapshot.revert(vms = vms, snapshot_name="1.7.5.9")
        if rc.startswith("1.8"):
            LibvirtManager.Snapshot.revert(vms = vms, snapshot_name="1.8.1.6")
        else:
            print (f"Указанная версия не поддерживается")
            return(-1)
        for vm in vms:
            task[vm] = {
                "prepare":{
                    'command': f"echo -e '{repo}' | sudo tee /etc/apt/sources.list",
                    'signal set': 'prepare',
                    'signal get': ''
                },
                "astra_update":{
                    'command': f"sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive astra-update -A -T -r",
                    'signal set': '',
                    'signal get': ['prepare']
                },                   
            }
        Libvirt.execute(commands=task, vms_dates=vms_dates, username="u", password='1', )
        LibvirtManager.Snapshot.create(vms=vms, snapshot_name=rc)
        

    def stop(vms: list):
        LibvirtManager.Vm.stop(vms=vms)

    def start(vms: list):
        LibvirtManager.Vm.start(vms=vms)


