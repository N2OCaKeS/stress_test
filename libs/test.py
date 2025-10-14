from allta import Libvirt, LibvirtManager

Libvirt.prepare()


vms_dates = {
    "testvm1": {
        "cpu": "4",
        "ram": "4096",
        "ip_bridge": "10.177.103.180",
        "disk": "100"
    }      
}

vms=['testvm1']

Libvirt.build(box="1.8.1.s", rc="1.8.1.6", vms=vms, vms_dates=vms_dates, bridge=True)

# LibvirtManager.Vm.bridge(vms_date=old, new_vms_date=vms_dates, username="u", password="1")

