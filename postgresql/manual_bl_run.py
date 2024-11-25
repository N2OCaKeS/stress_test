import argparse
import json
import requests
from balance.bl_lib import VBox, LVirt, bl, system
from psb_conf import REPORT_PATH







provider = VBox

parser = argparse.ArgumentParser()
parser.add_argument('-vbox', '--set-vbox',
                    action='store',
                    required=True,
                    help='set-vbox to vm',
                    dest='SET_BOX')




args = parser.parse_args()
astra_config_url = 'http://allta.devos.astralinux.ru/rest/api/get-box-config'
response_ac = requests.get(astra_config_url)
if response_ac.status_code == 200:
    with open('box-config.json', 'wb') as acb:
        acb.write(response_ac.content)
else:
    print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

with open('box-config.json', 'r') as r:
    dates = json.loads(r.read())


box_name, box_url = bl.box_wrapper(args.SET_BOX, dates)
kernel = system.check_output_command('uname -r')
vms = ['database1', 'database2', 'database3', 'lbdb1', 'lbdb2', 'lbdb3', 'dcfreeipa']
ansible_commands = [
    'cd balance && ansible-playbook bl_contrprimer.yml -vvv > bl_contrprimer.log',
    'cd balance && ansible-playbook tasks/checks/db/replication.yml -vvv > replication.log',
    'cd balance && ansible-playbook tasks/checks/db/load_balancing.yml -vvv > load_balancing.log',
    'cd balance && ansible-playbook tasks/tests/HA_DB_upgrade/high_availability_db_upgrade.yml -vvv > high_availability_db_upgrade.log'
]

vm_dates = {
        'database1':{'host-port':'2021',
                     'ip':'10.0.0.11',
                     'sshnum':'',
                     'ip_bridge':'10.177.103.111'},
        'database2':{'host-port':'2022',
                     'ip':'10.0.0.12',
                     'sshnum':'1',
                     'ip_bridge':'10.177.103.112'},
        'database3':{'host-port':'2025',
                     'ip':'10.0.0.13',
                     'sshnum':'2',
                     'ip_bridge':'10.177.103.113'},
        'lbdb1':{'host-port':'2024',
                 'ip':'10.0.0.41',
                 'sshnum':'3',
                 'ip_bridge':'10.177.103.141'},
        'lbdb2':{'host-port':'2023',
                 'ip':'10.0.0.42',
                 'sshnum':'4',
                 'ip_bridge':'10.177.103.142'},
        'lbdb3':{'host-port':'2026',
                 'ip':'10.0.0.43',
                 'sshnum':'5',
                 'ip_bridge':'10.177.103.143'},
        'pgpool':{'host-port':'2027',
                  'ip':'10.0.0.31',
                  'sshnum':'6',
                  'ip_bridge':'10.177.103.131'},
        'dcfreeipa':{'host-port':'2029',
                     'ip':'10.0.0.10',
                     'sshnum':'7',
                     'ip_bridge':'10.177.103.110'}
}


provider.prepare()
provider.build(box_name=box_name, box_url=box_url, kernel=kernel, rc=args.SET_BOX, vms=vms)
provider.check(vm_dates=vm_dates, vms=vms)
provider.execute(ansible_commands=ansible_commands, vm_dates=vm_dates, vms=vms)


