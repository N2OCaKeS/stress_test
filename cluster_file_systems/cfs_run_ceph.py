import argparse
from time import sleep
from libs.libcfs import check_output_command, send_remote_command, create_remote_file, get_remote_file

from cfs_create_vms import VMS
from cfs_storage_init_ceph import CephStorageCreate
from cfs_conf import STORAGE_NAME, SCRIPT_DIR, REPORT_DIR_HOST, REPORT_PATH_HOST
from libs.libtable import Report
from libs.zefir import UploaderZC

def parse_args():
    
    DESCRIPTION = ""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('-u', '--username',
                        action='store',
                        required=True,
                        help='confluence user',
                        dest='USER')

    parser.add_argument('-t', '--token',
                        action='store',
                        required=False,
                        default=None,
                        help='confluence access token',
                        dest='TOKEN')

    parser.add_argument('-cs', '--confluence-space',
                        action='store',
                        required=True,
                        help='confluence space',
                        dest='SPACE')

    parser.add_argument('-cpp', '--confluence-parent-page',
                        action='store',
                        required=True,
                        help='confluence parent page',
                        dest='PPAGE')

    parser.add_argument('-cnp', '--confluence-new-page',
                        action='store',
                        required=True,
                        help='confluence new page',
                        dest='NPAGE')

    parser.add_argument('-sn', '--stand-num',
                        action='store',
                        choices=['1',
                                '2',
                                '3',
                                '4'],
                        required=True,
                        help='stand num',
                        dest='STAND')

    parser.add_argument('-fti', '--folder-tree-id',
                        action='store',
                        required=True,
                        help='folder-tree-id',
                        dest='FTI')

    parser.add_argument('-tcyc', '--test-cycle-name',
                        action='store',
                        required=True,
                        help='test-cycle-name',
                        dest='TCYC')

    parser.add_argument('-tcas', '--test-case-name',
                        action='store',
                        required=True,
                        help='test-case-name',
                        dest='TCAS')

    parser.add_argument('-ba', '--basic-auth',
                        action='store',
                        required=True,
                        help='basic-auth',
                        dest='BA')

    parser.add_argument('-tcv', '--test-cycle-version',
                        action='store',
                        required=True,
                        help='test-cycle-version',
                        dest='TCV')
    
    parser.add_argument('--disk-size',
                        action='store',
                        required=False,
                        type=str,
                        default='25',
                        help='size of vdi disk',
                        dest='DISK_SIZE')

    parser.add_argument('--test-set',
                        action='store',
                        choices=['base_load',
                                 'timeout',
                                 'multithreaded',
                                 'big_files',
                                 'fs_mark_count',
                                 'fs_mark_size'],
                                 default='fs_mark_count',
                                 required=False,
                                 dest='TS')
    
    parser.add_argument('-vbox', 
                        action='store',
                        required=True,
                        help='vbox name',
                        dest='VBOX')

    parser.add_argument('-kernel', 
                        action='store',
                        required=True,
                        help='vbox name',
                        dest='KERNEL')

    return parser.parse_args()


class Ceph:

    restore_snapshot = 'virsh --connect qemu:///system snapshot-revert {host} {snapshot}'
    storagecreate = 'cd /var/lib/libvirt/images && sudo qemu-img create -f qcow2 cluster_storage{number} {size}G'
    storageattach = "virsh --connect qemu:///system attach-disk {node} --source /var/lib/libvirt/images/cluster_storage{number} --target {storage_name} --persistent --driver qemu --subdriver qcow2 --type disk"
    startvm = 'virsh --connect qemu:///system start {host}'
    controlvm_off = 'virsh --connect qemu:///system destroy {host}'
    run_test_cmd = 'sudo python3 {dir}/{file} --test-set {ts}'

    def __init__(self, vbox, kernel, all_hosts, type_load_test, **kwargs):
        self.vbox = vbox
        self.kernel = kernel
        self.all_hosts = all_hosts
        self.type_load_test = type_load_test
        for key, value in kwargs.items():
            setattr(self, key, value)
        # TODO 
        uzs = UploaderZC(folder_tree_id=args.FTI,
                         test_cycle_name=args.TCYC,
                         test_case_name=args.TCAS,
                         basic_auth=args.BA,
                         test_cycle_version=args.TCV,
                         token=args.TOKEN,
                         username=args.USER,
                         conf_space=args.SPACE,
                         conf_parent_page=args.PPAGE,
                         conf_new_page_name=args.NPAGE,
                         grade_stand=args.STAND,
                         file_system=args.FS,
                         test_set=args.TS)
    
        uzs.upload_test_cycle_status('progress')


    def start(self):
        if self.vbox.startswith("1.8"):
            self.vmc = 5
            install_need_packages = "sudo apt install python3-pip libgfapi0 libnbd0 libpmemblk1 -y"
            install_pip_req = "sudo pip3 install -r /var/tmp/req.txt --break-system-packages"
        else:
            self.vmc = 4
            install_need_packages = "sudo apt install libgfapi0 -y"
            install_pip_req = "sudo pip3 install -r /var/tmp/req.txt"
        
        # TODO
        HOST_IP = "10.177.103.101"
        virt_machines = VMS(rc_vbox=self.vbox, vm_count=len(self.all_hosts), hostip=HOST_IP, kernel=self.kernel)
        virt_machines.prepare_and_start()
        self.HOSTS = virt_machines.vm_dates
        for ind, node in enumerate(self.all_hosts):
            check_output_command(self.storagecreate.format(size="25", number=ind))
            sleep(20)
            check_output_command(self.storageattach.format(node=node, number=ind, storage_name=STORAGE_NAME))
            sleep(10)
            check_output_command(self.startvm.format(host=node))
            sleep(10)
        
        create_remote_file(local_file_path="libs", 
                            remote_file_path="/var/tmp/libs", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['login'], 
                            password=self.HOSTS["testvm1"]['password'])
        
        create_remote_file(local_file_path="fio", 
                            remote_file_path="/var/tmp/fio", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['login'], 
                            password=self.HOSTS["testvm1"]['password'])
        
        create_remote_file(local_file_path="cfs_test_ceph_fio.py", 
                            remote_file_path="/var/tmp/cfs_test_ceph_fio.py", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['login'], 
                            password=self.HOSTS["testvm1"]['password'])
        
        create_remote_file(local_file_path="fs_mark-3.3", 
                            remote_file_path="/var/tmp/fs_mark-3.3", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['login'], 
                            password=self.HOSTS["testvm1"]['password'])
        
        create_remote_file(local_file_path="cfs_test.py", 
                            remote_file_path="/var/tmp/cfs_test.py", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['login'], 
                            password=self.HOSTS["testvm1"]['password'])
        
        create_remote_file(local_file_path="cfs_conf.py", 
                            remote_file_path="/var/tmp/cfs_conf.py", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['login'], 
                            password=self.HOSTS["testvm1"]['password'])
        
        create_remote_file(local_file_path="req.txt", 
                            remote_file_path="/var/tmp/req.txt", 
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['login'], 
                            password=self.HOSTS["testvm1"]['password'])
        
        make_need_dir = "sudo mkdir /var/tmp/report /var/tmp/log"
        change_script_dir = "sed -i \"s|SCRIPT_DIR = '/git'|SCRIPT_DIR = '/var/tmp'|g\" /var/tmp/cfs_conf.py"

        send_remote_command(f"{make_need_dir} ; {install_need_packages} ; {install_pip_req} ; {change_script_dir}",
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['login'], 
                            password=self.HOSTS["testvm1"]['password'])

        if self.type_load_test == "fio":
            self.type_interface_ceph = "rbd"
        elif self.type_load_test == "fsmark":
            self.type_interface_ceph = "cephfs"
        storage = CephStorageCreate(astra_version=self.vbox, 
                                    HOSTS=self.HOSTS,
                                    type_interface_ceph=self.type_interface_ceph)
        storage.create_storage()
        
        if self.type_load_test == "fio":
            send_remote_command(command=f"cd /var/tmp && sudo python3 cfs_test_ceph_fio.py -abv {self.vbox}",
                                ip=self.HOSTS["testvm1"]['ip'], 
                                user=self.HOSTS["testvm1"]['login'], 
                                password=self.HOSTS["testvm1"]['password'])
            get_remote_file(remote_file_path="/var/tmp/report",
                            local_file_path=f"{REPORT_DIR_HOST}",
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['login'], 
                            password=self.HOSTS["testvm1"]['password'])

        else:
            send_remote_command(command=f'sudo chmod +x /var/tmp/fs_mark-3.3/fs_mark && {self.run_test_cmd.format(dir="/var/tmp", file="cfs_test.py", ts="fs_mark_count")}',
                                ip=self.HOSTS["testvm1"]['ip'], 
                                user=self.HOSTS["testvm1"]['login'], 
                                password=self.HOSTS["testvm1"]['password'])
            get_remote_file(remote_file_path="/var/tmp/report",
                            local_file_path=f"{REPORT_DIR_HOST}",
                            ip=self.HOSTS["testvm1"]['ip'], 
                            user=self.HOSTS["testvm1"]['login'], 
                            password=self.HOSTS["testvm1"]['password'])
        
        self.uzs.public = True
        self.uzs.statistics = True
        self.uzs.upload_test_cycle_status(zefir_status='pass')

if __name__ == "__main__":
    args = parse_args()
    args_dict = vars(args)

    c = Ceph(vbox="1.8.3.7",
             kernel="6.1",
             all_hosts=["testvm1", "testvm2", "testvm3", "testvm4", "testvm5"],
             type_load_test="fio",
             **args_dict)
    c.start()