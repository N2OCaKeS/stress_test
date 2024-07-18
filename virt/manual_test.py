from libs.virtlib import cmd

#TODO 
#add vagrant user in boxes

vm_count = 1
vcpu = 8
ram = 8192

box_name = 'debian'
box_url = 'ftp://10.177.103.10/boxes/box/debian.box'

cmd(f'vagrant box add --provider virtualbox {box_name} {box_url}')
cmd(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')

# add define pool
try:
    cmd('virsh pool-define-as --name default --type dir --target /var/lib/libvirt/images')
    cmd('virsh pool-autostart default')
    cmd('virsh pool-start default')
except Exception as e:
    print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

# create_vm
print('UPDATE={} BOX_URL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(box_name,
                                                                                         box_url,                                                                                                            
                                                                                         vm_count,
                                                                                         vcpu,
                                                                                         ram))
cmd('UPDATE={} BOX_URL={} COUNT={} CPU={} RAM={} vagrant up --provider=libvirt'.format(box_name,
                                                                                       box_url,
                                                                                       vm_count,
                                                                                       vcpu,
                                                                                       ram))