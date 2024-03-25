from libs.virtlib import check_output_command, cmd


box_name = 'smolensk-vanilla-gui/1.8.0.13'
box_url = 'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.8.0.json'

def add_box():
    cmd(f'vagrant box add --provider virtualbox {box_name} {box_url}')
    cmd(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')

