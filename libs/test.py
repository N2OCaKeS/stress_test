from allta import Libvirt, LibvirtManager

Libvirt.prepare()


vms_dates = {
    "testvm1": {
        "cpu": "4",
        "ram": "4096",
        "ip_bridge": "10.177.103.180",
        "disk": "100g"
    }
}
for vm in vms_dates:
    a = vms_dates.get(vm, {}) or {}
    print(a.get("disk"))
vms=['testvm1']

old = Libvirt.build(box="debian12", rc="1.7.5", vms=vms, vms_dates=vms_dates)

LibvirtManager.Vm.bridge(vms_date=old, new_vms_date=vms_dates, username="u", password="1")