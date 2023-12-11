import subprocess


def cmd(command):
    return subprocess.run(command, shell=True)

box_url = 'http://qa111.devos.astralinux.ru/vault/vagrant/smol-1.8.0.json'
box_name = 'smolensk-vanilla-gui/1.8.0.2'

cmd('sudo bash bl_prepare.sh')
cmd(f'vagrant box add {box_url} --force')
cmd(f'UPDATE={box_name} vagrant up --provider=virtualbox')


