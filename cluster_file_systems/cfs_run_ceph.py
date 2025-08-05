from time import sleep
from libs.libcfs import check_output_command, send_remote_command

from cfs_create_vms import VMS
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

    def __init__(self, vbox, kernel, all_hosts):
        self.vbox = vbox
        self.kernel = kernel
        self.all_hosts = all_hosts


    def start(self):
        if self.vbox.startswith("1.8"):
            self.vmc = 5 
        else:
            self.vmc = 4

        virt_machines = VMS(rc_vbox=self.vbox, vm_count=len(self.all_hosts), hostip=HOST_IP, kernel=self.kernel)
        virt_machines.prepare_and_start()

        for ind, node in enumerate(self.all_hosts):
            check_output_command(self.storagecreate.format(size=..., number=ind))
            sleep(20)
            check_output_command(self.storageattach.format(node=node, storage_name=STORAGE_NAME))


        storage = CephStorageCreate(astra_version=self.vbox, HOSTS=virt_machines.vm_dates)
        storage.create_storage()
        #### TODO
        send_remote_command(self.run_test_cmd.format(dir=..., file="cfs_test.py", ts=...),
                            ip=...,
                            user=...,
                            password=...)

        #### TODO
        report = Report()


