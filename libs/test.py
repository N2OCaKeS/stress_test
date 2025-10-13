from allta import Libvirt, LibvirtManager

# Libvirt.prepare()
    
a = Command()

command = ["sudo ls /", "sudo ls ~", "sudo ls ~"]

cm = a.generator(command=command, target="test", sync=True)

print(cm)

# vms_dates = {
#     "testvm1": {
#         "cpu": "4",
#         "ram": "4096",
#         "ip_bridge": "10.177.103.180",
#         "disk": "100"
#     }      
# }

# vms=['testvm1']

# Libvirt.build(box="debian12", rc="1.7.5", vms=vms, vms_dates=vms_dates)

# LibvirtManager.Vm.bridge(vms_date=old, new_vms_date=vms_dates, username="u", password="1")

