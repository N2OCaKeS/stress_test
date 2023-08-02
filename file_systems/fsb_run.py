# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess


from sys import exit
from time import sleep, time, strftime, gmtime, ctime
from os import getuid, path, mkdir
from fabric import Connection
from libs.libfsb import astra_version, upload_results_to_ftp
from libs.zefir import UploaderZC
from fsb_conf import MACHINE_POSTFIX, SNAPSHOT_NAME, \
    HOSTS, USER, PASSWORD, SCRIPT_DIR, LOG_FILENAME, REPORT_PATH, STORAGE_MOUNT_DIR, \
    INFO_FILENAME, PACKAGES, REPORT_FILENAME

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('--virtual',
                    action='store_true',
                    required=False,
                    help='host type',
                    dest='VIRTUAL')

parser.add_argument('--disk-size',
                    action='store',
                    required=False,
                    type=str,
                    default='5120',
                    help='size of vdi disk',
                    dest='DISK_SIZE')

parser.add_argument('-fs', '--file-system',
                    action='store',
                    choices=['ext2',
                             'ext3',
                             'ext4',
                             'fat',
                             'ntfs',
                             'xfs'],
                    required=True,
                    help='filesystem',
                    dest='FS')

parser.add_argument('--host',
                    action='store',
                    choices=['fidcm',
                             'stand1',
                             'stand2'],
                    required=False,
                    help='hostname where the storage is located',
                    dest='HOST')

parser.add_argument('-ts', '--test-set',
                    action='store',
                    choices=['base_load',
                             'timeout',
                             'multithreaded',
                             'big_files',
                             'fs_mark_count',
                             'fs_mark_size'],
                    default='fs_mark_count',
                    required=False,
                    dest='TS')

parser.add_argument('--parsec',
                    action='store_true',
                    required=False,
                    help='',
                    dest='PARSEC')

parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

parser.add_argument('-t', '--token',
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

parser.add_argument('-sn', '--stand-num',
                    action='store',
                    choices=['1',
                             '2',
                             '3',
                             '4'],
                    required=True,
                    help='stand num',
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

parser.add_argument('-tcv', '--test-cycle-version',
                    action='store',
                    required=True,
                    help='test-cycle-version',
                    dest='TCV')

args = parser.parse_args()


uzs = UploaderZC(folder_tree_id=args.FTI,
                 test_cycle_name=args.TCYC,
                 test_case_name=args.TCAS,
                 basic_auth=args.BA,
                 test_cycle_version=args.TCV,
                 token=args.TOKEN,
                 username=args.USER,
                 conf_space=args.SPACE,
                 conf_parent_page=args.PPAGE,
                 conf_new_page_name=args.NPAGE,
                 grade_stand=args.STAND,
                 file_system=args.FS)
    
uzs.upload_test_cycle_status('progress')

'''
    main
'''
run_storage_init = 'sudo python3 {dir}/fsb_storage_init.py --fs {fs}'
run_test = 'sudo python3 {dir}/fsb_test.py --test-set {ts}'
run_test_parsec = 'sudo python3 {dir}/fsb_test.py --test-set {ts} --parsec'

'''
    VirtualBox
'''
vm_dir = '/home/$USER/VirtualBox\ VMs/'
vm_restore_snapshot = 'VBoxManage snapshot {host}_{postfix} restore {shapshot}'
vm_storage_create = 'VBoxManage createmedium disk --filename {dir}{fs}_storage --size {size} --format VDI --variant Standard'
vm_storage_detache = ''
vm_storage_remove = 'rm -rf {dir}{fs}_storage.vdi'
vm_storage_attach = 'VBoxManage storageattach {host}_{postfix} --storagectl "SATA Controller" --port 2 --device 0 --type hdd --medium {dir}{fs}_storage.vdi'
vm_power_on = 'VBoxManage startvm {host}_{postfix} --type headless'
vm_power_off = 'VBoxManage controlvm {host}_{postfix} poweroff'


def cmd(command):
    subprocess.run(command,
                   shell=True,
                   stderr=subprocess.DEVNULL)


def host_is_available(node):
    try:
        if args.VIRTUAL:  # вирт. стенд
            with Connection(host='127.0.0.1',
                            port=HOSTS[node]['port'],
                            user=USER,
                            connect_kwargs={"password": PASSWORD}) as node_client:
                if str(node_client.run('uptime')):
                    return True
        else:
            pass
    except Exception:
        return False


start_time = time()

if args.PARSEC and 'ext' not in args.FS:
    print('"--parsec" is only ext* file systems')
    exit(2)

if args.VIRTUAL: # вирт. стенд
    '''
        Создать тестовый диск заданного размера. 
        Подключить к машине тестовый диск. 
        Запустить машину.
    '''
    # Восстановить последний актуальные снимки
    cmd(vm_restore_snapshot.format(host=args.HOST,
                                   postfix=MACHINE_POSTFIX,
                                   shapshot=SNAPSHOT_NAME))
    # Создать тестовый накопитель
    cmd(vm_storage_create.format(fs=args.FS,
                                 dir=vm_dir,
                                 size=args.DISK_SIZE))

    # Подключить тестовый накопитель
    cmd(vm_storage_attach.format(host=args.HOST,
                                 postfix=MACHINE_POSTFIX,
                                 dir=vm_dir,
                                 fs=args.FS))

    # Запустить виртуальную машину
    cmd(vm_power_on.format(host=args.HOST,
                           postfix=MACHINE_POSTFIX))

    # Дождаться окончания загрузки
    while host_is_available(args.HOST) is False:
        sleep(1)
    sleep(10)

    '''
        Запустить скрипт настройки тестовой машины.
    '''
    try:
        with Connection(host='127.0.0.1',
                        port=HOSTS[args.HOST]['port'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as storage_host_client:
            storage_host_client.run(run_storage_init.format(dir=SCRIPT_DIR,
                                                            fs=args.FS))
    except Exception as exception:
        print("\033[91mНе удалось настроить стенд.\033[0m")
        print(exception)
        exit(2)

    '''
        Начать тестирование. 
        Запустить прогон.
    '''
    # Очистить лог
    log_file = open(LOG_FILENAME, 'w')
    log_file.close()

    try:
        with Connection(host='127.0.0.1',
                        port=HOSTS[args.HOST]['port'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as node_client:
            if args.PARSEC:
                node_client.run(run_test_parsec.format(dir=SCRIPT_DIR,
                                                       ts=args.TS))
            else:
                node_client.run(run_test.format(dir=SCRIPT_DIR,
                                                ts=args.TS))
    except Exception as exception:
        print("\033[91m Тестирование завершилось исключением.\033[0m")
        print(exception)
        exit(2)
    '''
        Выключить машину.
    '''
    cmd(vm_power_off.format(host=args.HOST, postfix=MACHINE_POSTFIX))
    cmd(vm_storage_remove.format(fs=args.FS, dir=vm_dir))
else: # физ. стенд
    '''
        Запустить скрипт настройки тестовой машины.
    '''
    try:
        cmd(run_storage_init.format(dir=SCRIPT_DIR,
                                    fs=args.FS,
                                    host=args.HOST))
    except Exception as exception:
        print("\033[91mНе удалось настроить стенд.\033[0m")
        print(exception)
        exit(2)
    '''
        Начать тестирование. 
        Запустить прогон.
    '''
    # Очистить лог
    log_file = open(LOG_FILENAME, 'w')
    log_file.close()

    log_file = open(INFO_FILENAME, 'w')
    log_file.close()

    # Создать /report
    if not path.exists(REPORT_PATH):
        mkdir(REPORT_PATH, mode=0o755)

    try:
        if args.PARSEC:
            cmd(run_test_parsec.format(dir=SCRIPT_DIR,
                                       ts=args.TS))
        else:
            cmd(run_test.format(dir=SCRIPT_DIR,
                                ts=args.TS))
    except Exception as exception:
        print("\033[91m Тестирование завершилось исключением.\033[0m")
        print(exception)
        exit(2)

    cmd('umount {}'.format(STORAGE_MOUNT_DIR))


lead_time = strftime("%H:%M:%S", gmtime(time() - start_time))
print('lead time: {t}'.format(t=lead_time))

# собрать системную информацию
info_lst = ['{digit_v}({mode})\n'.format(digit_v=astra_version()[0], mode=astra_version()[1]),
            subprocess.run('uname -r',
                           shell=True,
                           stdout=subprocess.PIPE).stdout.decode("utf-8"),
            subprocess.run("dpkg -l " + PACKAGES[args.FS] + " | awk '{print $3}' | tail -n1",
                           shell=True,
                           stdout=subprocess.PIPE).stdout.decode("utf-8"),
            str(lead_time)]

with open(INFO_FILENAME, 'a+') as info:
    info.writelines(info_lst)

upload_results_to_ftp(args.TCV, f'{REPORT_PATH}/{REPORT_FILENAME}', f'{args.FS}_{args.TCYC}_{REPORT_FILENAME}')

uzs.public = True
uzs.statistics = True
uzs.upload_test_cycle_status(zefir_status='pass')

if path.isfile('libs/zefir.log'):
    with open('libs/zefir.log', 'r') as r:
        zefir_log = r.read()
        print('\n\n\nZefir-log\n')
        print(zefir_log)
if path.isfile('JIRA_ERROR.log'):
    with open('JIRA_ERROR.log', 'r') as r:
        jira_log = r.read()
        print('\n\n\nJira-log\n')
        print(jira_log)
