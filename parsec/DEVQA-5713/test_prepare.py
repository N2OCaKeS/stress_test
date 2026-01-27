from time import sleep


def create_vms_test_env(mode='s',
                        user='u',
                        key=None):
    """
    Reqiered python >= 3.12
    """
    VMS = ['testvm1', 'testvm2']
    VMS_DATES = {
        VMS[0]: {'host-port': '22', 
                'cpu': '8', 
                'ram': '32768',
                'ip_bridge': '10.177.103.77'      
        },
        VMS[1]: {'host-port': '22', 
                'cpu': '8', 
                'ram': '32768',
                'ip_bridge': '10.177.103.78'   
                }
    }


    from allta import Libvirt, LibvirtManager, SystemCommands
    provider = Libvirt()

    provider.prepare()
    vm_date = provider.build(f'1.8.1.{mode}', '1.8.4.48', VMS, VMS_DATES, kernel='6.1.152-1-generic')
    sleep(90)
    LibvirtManager.Vm.bridge(vms_date=vm_date, new_vms_date=VMS_DATES, username="u", password="1")
    LibvirtManager.Snapshot.create(VMS, snapshot_name='snap1')

    LibvirtManager.Vm.stop(vms=VMS)
    print(SystemCommands.check_output_command('sudo sed -i \'s#<forward mode="nat"/>#<forward mode="none"/>#\' "/vms/network.xml"'))
    print(SystemCommands.check_output_command("sudo virsh net-destroy test"))
    print(SystemCommands.check_output_command("sudo virsh --connect qemu:///system net-create /vms/network.xml"))
    LibvirtManager.Vm.start(vms=VMS)

    LibvirtManager.Snapshot.create(VMS, snapshot_name='snap2')

    