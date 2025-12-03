from __future__ import annotations

from time import sleep

from allta import BaseDecorators, Libvirt, LibvirtManager

vms_dates = {
    "testvm1": {
        "ip_bridge": "10.177.103.158",        
        "cpu": "4",
        "ram": "4096",
        "disk": "100",
        "additional_disks": {
            "disk1": {
                "size": "100",  # default 10 gb
                "mount_point": "/home/testuser2",  # default none, if default then not mount in vm
                "fs_type": "ext4",  # default ext4
            },
            "disk2": {
                "size": "100",  # default 10 gb
                "mount_point": "/home/testuser2",  # default none, if default then not mount in vm
                "fs_type": "ntfs",  # default ext4 FOR QCOW DISK
            },
            "disk3": {
                "device": "/dev/vdb",  # default none
                "fs_type": "ext4",  # default none FOR BLOCK DISK if default then NOT format
                "mount_point": "/vms",  # default none, if default then not mount in vm
            },
            "disk4": {
                "device": "/dev/vdc2",  # default none
            },
        },
    }
}
vms = list(vms_dates.keys())
box = "1.8.1.o"
rc = "1.8.1.6"


class Libvirt_test:
    @staticmethod
    def build():
        Libvirt.prepare()
        Libvirt.build(box=box, rc=rc, vms=vms, vms_dates=vms_dates, bridge=True)

    @staticmethod
    def build_old():
        old = Libvirt.build(box=box, rc=rc, vms=vms, vms_dates=vms_dates, bridge=False)
        LibvirtManager.Vm.bridge(
            vms_date=old, new_vms_date=vms_dates, username="u", password="1"
        )

    @staticmethod
    def check():
        Libvirt.check(vms=vms, vms_dates=vms_dates)

    @staticmethod
    def execute():
        example_task1 = {
            "testvm1": {
                "prepare_task": {
                    "command": "sudo apt-get install iperf -y",
                    "signal set": "1",
                }
            }
        }
        Libvirt.execute(commands=example_task1, vms_dates=vms_dates)

    @staticmethod
    def execute_no_wait():
        example_task1 = {
            "testvm1": {
                "prepare_task": {
                    "command": "sudo apt-get install linux-tools-$(uname -r) -y",
                    "signal set": "1",
                },
                "test_task": {
                    "command": "sudo perf record -g -a &",
                    "signal get": "1",
                    "nowait": True,  # default = False
                    "nowait_timeout": 3,  # default = 30 sec
                },
            }
        }
        Libvirt.execute(commands=example_task1, vms_dates=vms_dates)

    @staticmethod
    def scp():
        scp = {
            "testvm1": [
                {"mode": "push", "path_host": "test.py", "path_vm": "/home/test.py"}
            ]
        }
        Libvirt.scp(scp_settings=scp, vms_dates=vms_dates)


class LibvirtManager_test:
    @staticmethod
    def additional_disk():
        return LibvirtManager.Vm.additional_disk(
            vms_dates=vms_dates, disk_path="/home/u"
        )


class Decorators_test:
    @staticmethod
    @BaseDecorators.timer
    def timer():
        sleep(0.000234)


# Libvirt_test.build()
# LibvirtManager_test.additional_disk()

