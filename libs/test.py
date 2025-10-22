from allta import Libvirt, LibvirtManager

vms_dates = {
    "testvm1": {"cpu": "4", "ram": "4096", "ip_bridge": "10.177.103.180", "disk": "100"}
}


vms_dates = {
    "testvm1": {
        "cpu": "4",
        "ram": "4096",
        "ip_bridge": "10.177.103.180",
        "disk": "100",
        "additional_disks": {
            "disk1": {
                "size": "100",  # default 10 gb
                "mount_point": "/home/testuser",  # default none, if default then not mount in vm
            },
            "disk2": {
                "size": "100",  # default 10 gb
                "mount_point": "/home/testuser2",  # default none, if default then not mount in vm
                "fs_type": "ntfs",  # default ext4
            },
            "disk3": {
                "size": "10",  # default 10 gb
            },
        },
    }
}
vms = list(vms_dates.keys())


def build():
    Libvirt.prepare()
    Libvirt.build(
        box="1.8.1.s", rc="1.8.1.6", vms=vms, vms_dates=vms_dates, bridge=True
    )


def build_old():
    old = Libvirt.build(
        box="1.8.1.s", rc="1.8.1.6", vms=vms, vms_dates=vms_dates, bridge=False
    )
    LibvirtManager.Vm.bridge(
        vms_date=old, new_vms_date=vms_dates, username="u", password="1"
    )

def check():
    Libvirt.check(vms=vms, vms_dates=vms_dates)

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


def scp():
    scp = {
        "testvm1": [
            {"mode": "push", "path_host": "test.py", "path_vm": "/home/test.py"}
        ]
    }
    Libvirt.scp(scp_settings=scp, vms_dates=vms_dates)


def additional_disk():
    return LibvirtManager.Vm.additional_disk(vms_dates=vms_dates, disk_path="/home/u")
    


# build()
check()
# additional_disk()
