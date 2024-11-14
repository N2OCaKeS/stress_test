import argparse
import json
import requests
from balance.bl_lib import VBox, LVirt, bl
from libs.zefir import UploaderZC
from psb_conf import REPORT_PATH
import pandas
import os



"""
INFO

pgpool2 имеет некоторую особенность, что при совершении failover или failback он на короткий промежуток времени запрещает 
подключение пользователей, из-за чего pgbench завершается с ошибкой, и не получается идеальной доступности сервиса.
Поэтому вместо использования pgbench был написан свой скрипт для симуляции клиентов, который спокойно переваривает ошибки 
подключения и просто записывает их как неудачные запросы. 
Оказалось, что даже при текущем поведении в failover/failback получается не так уж и много запросов не обрабатывается 
кластером (при обновлении 3 БД со связкой с 3-мя балансировщиками), всего не больше 150 при общем количестве запросов 
около 67000, что составляет в среднем не больше 0.2% проваленных запросов. Кроме того, ниже я скинул ссылку на коммит 
из Github репозитория pgpool2, где наконец летом этого года кто-то занялся этой проблемой с запретом подключения 
пользователей, так что в будущем можно ожидать, что процент проваленных запросов при обновлении будет ещё меньше.
https://github.com/pgpool/pgpool2/commit/4aa657e055250da9db9a4c5cde7260e8f24707cb
"""


provider = VBox
"""
SETTINGS

В зависимости от требований нужно выбрать необходимый провайдер для виртуальных машин, в которых будет проходить тест:   
1. Virtualbox: VBox
2. LibVirt:    LVirt
"""



parser = argparse.ArgumentParser()
parser.add_argument('-sn',
                    action='store',
                    required=True,
                    help='stand number',
                    dest='STAND')

parser.add_argument('-fti', '--folder-tree-id',
                    action='store',
                    required=True,
                    help='folder-tree-id',
                    dest='FTI')

parser.add_argument('-tcyc', '--test-cycle-name',
                    action='store',
                    required=True,
                    help='test-cycle-name',
                    dest='TCYC')

parser.add_argument('-tcas', '--test-case-name',
                    action='store',
                    required=True,
                    help='test-case-name',
                    dest='TCAS')

parser.add_argument('-ba', '--basic-auth',
                    action='store',
                    required=True,
                    help='basic-auth',
                    dest='BA')

parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

parser.add_argument('-tk', '--token',
                    action='store',
                    required=False,
                    default=None,
                    help='confluence access token',
                    dest='TOKEN')

parser.add_argument('-cs', '--confluence-space',
                    action='store',
                    required=True,
                    help='confluence space',
                    dest='SPACE')

parser.add_argument('-cpp', '--confluence-parent-page',
                    action='store',
                    required=True,
                    help='confluence parent page',
                    dest='PPAGE')

parser.add_argument('-cnp', '--confluence-new-page',
                    action='store',
                    required=True,
                    help='confluence new page',
                    dest='NPAGE')

parser.add_argument('-tcv', '--test-cycle-version',
                    action='store',
                    required=True,
                    help='test-cycle-version',
                    dest='TCV')

parser.add_argument('-vbox', '--set-vbox',
                    action='store',
                    required=True,
                    help='set-vbox to vm',
                    dest='SET_BOX')


"""
VARIABLES
"""

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
kernel = str(args.TCYC).split('_')[2]
vms = ['database1', 'database2', 'database3', 'lbdb1', 'lbdb2', 'lbdb3', 'dcfreeipa']
ansible_commands = [
    'cd balance && ansible-playbook bl_contrprimer.yml -vvv',
    'cd balance && ansible-playbook tasks/checks/db/replication.yml -vvv',
    'cd balance && ansible-playbook tasks/checks/db/load_balancing.yml -vvv',
    'cd balance && ansible-playbook tasks/tests/HA_DB_upgrade/high_availability_db_upgrade.yml -vvv'
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



"""
TEST
"""

uzs = UploaderZC(folder_tree_id=args.FTI,
                test_cycle_name=args.TCYC,
                test_case_name=args.TCAS,
                basic_auth=args.BA,
                test_cycle_version=args.TCV,
                token=args.TOKEN,
                username=args.USER,
                grade_stand=args.STAND,
                conf_space=args.SPACE,
                conf_parent_page=args.PPAGE,
                conf_new_page_name=args.NPAGE)
uzs.upload_test_cycle_status('progress')


bl.check(provider.prepare, uzs)
bl.check(provider.build, uzs, box_name=box_name, box_url=box_url, kernel=kernel, rc=args.SET_BOX, vms=vms)
bl.check(provider.check, uzs, vm_dates=vm_dates, vms=vms)
bl.check(provider.execute, uzs, ansible_commands=ansible_commands, vm_dates=vm_dates, vms=vms)


if os.path.isfile('results_balance.txt'):
    print('File "results_balance.txt" exist')
    with open('results_balance.txt', 'r') as r:
        results = dict(line.rstrip().split(':') for line in r)
        print(results)
    
    dates = {'Name':results.keys(),
             'Results':results.values()}
    
    if not os.path.exists(REPORT_PATH):
        os.mkdir(REPORT_PATH, mode=0o755)
    
    df = pandas.DataFrame(dates); print(df)
    df.to_html(f'{REPORT_PATH}/results_balance.html', index=False)

    uzs.public = True
    uzs.statistics = True
    uzs.balance = True
    uzs.upload_test_cycle_status(zefir_status='pass')
else:
    print('Fail! File "results_balance.txt" not found')
    uzs.upload_test_cycle_status(zefir_status='fail')


