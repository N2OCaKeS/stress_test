from time import sleep
from libs.libcfs import check_output_command, send_remote_command, create_remote_file

# from cfs_create_vms import VMS
from cfs_storage_init_ceph import CephStorageCreate
from cfs_conf import STORAGE_NAME, SCRIPT_DIR
from libs.libtable import Report

class Ceph:

    restore_snapshot = 'virsh --connect qemu:///system snapshot-revert {host} {snapshot}'
    storagecreate = 'cd /var/lib/libvirt/images && sudo qemu-img create -f qcow2 cluster_storage{number} {size}G'
    storageattach = "virsh --connect qemu:///system attach-disk {node} --source /var/lib/libvirt/images/cluster_storage --target {storage_name} --persistent --driver qemu --subdriver qcow2 --type disk"
    startvm = 'virsh --connect qemu:///system start {host}'
    controlvm_off = 'virsh --connect qemu:///system destroy {host}'
    run_test_cmd = 'sudo python3 {dir}/{file} --test-set {ts}'

    def __init__(self, vbox, kernel, all_hosts, type_load_test):
        self.vbox = vbox
        self.kernel = kernel
        self.all_hosts = all_hosts
        self.type_load_test = type_load_test
        self.HOSTS = {
            'testvm1': {
                'ip': "127.0.0.1",
                'user': "u",
                'password': "1",
                # 'port': 50020
                'port': 60001
                },
            'testvm2': {
                "ip": "127.0.0.1",
                "user": "u",
                "password": "1",
                "port": 60002
            },
            'testvm3': {
                "ip": "127.0.0.1",
                "user": "u",
                "password": "1",
                "port": 60003
            },
            'testvm4': {
                "ip": "127.0.0.1",
                "user": "u",
                "password": "1",
                "port": 60004
            },
            'testvm5': {
                "ip": "127.0.0.1",
                "user": "u",
                "password": "1",
                "port": 60005
            }
        }


    def start(self):
        if self.vbox.startswith("1.8"):
            self.vmc = 5 
        else:
            self.vmc = 4

        # virt_machines = VMS(rc_vbox=self.vbox, vm_count=len(self.all_hosts), hostip=HOST_IP, kernel=self.kernel)
        # virt_machines.prepare_and_start()
        # self.HOSTS = virt_machines.vm_dates
        # for ind, node in enumerate(self.all_hosts):
        #     check_output_command(self.storagecreate.format(size=..., number=ind))
        #     sleep(20)
        #     check_output_command(self.storageattach.format(node=node, storage_name=STORAGE_NAME))
        
        create_remote_file(local_file_path="libs", 
                            remote_file_path="/var/tmp/libs", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['user'], 
                            password=self.HOSTS["testvm1"]['password'],
                            port=self.HOSTS["testvm1"]['port'])
        
        create_remote_file(local_file_path="fio", 
                            remote_file_path="/var/tmp/fio", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['user'], 
                            password=self.HOSTS["testvm1"]['password'],
                            port=self.HOSTS["testvm1"]['port'])
        
        create_remote_file(local_file_path="cfs_test_ceph_fio.py", 
                            remote_file_path="/var/tmp/cfs_test_ceph_fio.py", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['user'], 
                            password=self.HOSTS["testvm1"]['password'],
                            port=self.HOSTS["testvm1"]['port'])
        
        create_remote_file(local_file_path="fs_mark-3.3", 
                            remote_file_path="/var/tmp/fs_mark-3.3", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['user'], 
                            password=self.HOSTS["testvm1"]['password'],
                            port=self.HOSTS["testvm1"]['port'])
        
        create_remote_file(local_file_path="cfs_test.py", 
                            remote_file_path="/var/tmp/cfs_test.py", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['user'], 
                            password=self.HOSTS["testvm1"]['password'],
                            port=self.HOSTS["testvm1"]['port'])
        
        create_remote_file(local_file_path="cfs_conf.py", 
                            remote_file_path="/var/tmp/cfs_conf.py", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['user'], 
                            password=self.HOSTS["testvm1"]['password'],
                            port=self.HOSTS["testvm1"]['port'])
        
        create_remote_file(local_file_path="req.txt", 
                            remote_file_path="/var/tmp/req.txt", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['user'], 
                            password=self.HOSTS["testvm1"]['password'],
                            port=self.HOSTS["testvm1"]['port'])
        
        make_need_dir = "sudo mkdir /var/tmp/report /var/tmp/log"
        install_need_packages = "sudo apt install python3-pip libgfapi0 libnbd0 libpmemblk1 -y"
        # sudo apt install libgfapi0 -y
        install_pip_req = "sudo pip3 install -r /var/tmp/req.txt --break-system-packages"

        send_remote_command(f"{make_need_dir} && {install_need_packages} && {install_pip_req}",
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['user'], 
                            password=self.HOSTS["testvm1"]['password'],
                            port=self.HOSTS["testvm1"]['port'])


        storage = CephStorageCreate(astra_version=self.vbox, 
                                    #TODO
                                    HOSTS=self.HOSTS,
                                    # HOSTS=virt_machines.vm_dates, 
                                    type_interface_ceph="cephfs")
        storage.create_storage()
        
        
        if self.type_load_test == "fio":
            pass
            send_remote_command(command=f"cd /var/tmp && sudo python3 cfs_test_ceph_fio.py -abv {self.vbox}",
                                ip=self.HOSTS["testvm1"]['ip'], 
                                user=self.HOSTS["testvm1"]['user'], 
                                password=self.HOSTS["testvm1"]['password'],
                                port=self.HOSTS["testvm1"]['port'])
            #### TODO
            # report = Report()
        else:
            pass
            #### TODO
            send_remote_command(command=f"sudo chmod +x /var/tmp/fs_mark-3.3/fs_mark && {self.run_test_cmd.format(dir="/var/tmp", file="cfs_test.py", ts="fs_mark_count")}",
                                ip=self.HOSTS["testvm1"]['ip'], 
                                user=self.HOSTS["testvm1"]['user'], 
                                password=self.HOSTS["testvm1"]['password'],
                                port=self.HOSTS["testvm1"]['port'])


if __name__ == "__main__":
    c = Ceph(vbox="1.8.2",
             kernel="6.1",
             all_hosts=["testvm1", "testvm2", "testvm3", "testvm4", "testvm5"],
             type_load_test="fio")
    c.start()



