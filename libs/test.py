from allta import Libvirt, LibvirtManager

# Libvirt.prepare()


vms_dates = {
    "testvm1": {
        "cpu": "4",
        "ram": "4096",
        "ip_bridge": "10.177.103.180"
    }
}
vms=['testvm1']

old = Libvirt.build(box="1.7.5.o", rc="1.7.5", vms=vms, vms_dates=vms_dates)

LibvirtManager.Vm.bridge(vms_date=old, new_vms_date=vms_dates, username="u", password="1")