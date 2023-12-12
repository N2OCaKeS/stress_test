import subprocess


def cmd(command):
    return subprocess.run(command, shell=True)

box_url_18 = 'http://qa111.devos.astralinux.ru/vault/vagrant/smol-1.8.0.json'
box_name_18 = 'smolensk-vanilla-gui/1.8.0.2'
box_url_174 = 'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.7.4.json'
box_name_174 = 'smolensk-vanilla-gui/1.7.4'


cmd('sudo bash bl_prepare.sh')
cmd(f'vagrant box add {box_url_174} --force')
cmd(f'UPDATE={box_name_174} vagrant up --provider=virtualbox')


