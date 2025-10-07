import argparse
from libs.zefir import UploaderZC
from psb_conf import REPORT_PATH
import pandas
import os
from new_balance import bl_lib


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
args = parser.parse_args()


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



"""
VARIABLES
"""

bl_lib.balance(args.TCV)




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
    #uzs.statistics = True
    uzs.balance = True
    uzs.upload_test_cycle_status(zefir_status='pass')
else:
    print('Fail! File "results_balance.txt" not found')
    uzs.upload_test_cycle_status(zefir_status='fail')
