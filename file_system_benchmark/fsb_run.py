# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess


from sys import exit
from time import sleep
from os import getuid, path, mkdir
from fabric import Connection
from fsb_conf import MACHINE_POSTFIX, SNAPSHOT_NAME, \
    HOSTS, USER, PASSWORD, SCRIPT_DIR, LOG_FILENAME, REPORT_PATH, STORAGE_MOUNT_DIR

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

parser.add_argument('--fs',
                    action='store',
                    choices=['ext2',
                             'ext3',
                             'ext4',
                             'fat',
                             'ntfs'],
                    required=True,
                    help='filesystem',
                    dest='FS')

parser.add_argument('--host',
                    action='store',
                    choices=['sudcm',
                             'stand1'],
                    required=True,
                    help='hostname where the storage is located',
                    dest='HOST')

parser.add_argument('--test-set',
                    action='store',
                    choices=['base_load',
                             'timeout',
                             'multithreaded',
                             'big_files',
                             'fs_mark_count',
                             'fs_mark_size'],
                    required=True,
                    dest='TS')

parser.add_argument('--data-from-config',
                    action='store_false',
                    required=False,
                    help='get data from config',
                    dest='CONFIG')

args = parser.parse_args()
'''
    main
'''
script_dir = SCRIPT_DIR
run_storage_init = 'sudo {dir}/venv/bin/python {dir}/fsb_storage_init.py --fs {fs}'
run_test = 'sudo {dir}/venv/bin/python {dir}/fsb_test.py --test-set {ts}'

'''
    VirtualBox
'''
vm_dir = '/home/$USER/VirtualBox\ VMs/'
vm_restore_snapshot = 'VBoxManage snapshot {host}_{postfix} restore {shapshot}'
vm_storage_create = 'VBoxManage createmedium disk --filename {dir}{fs}_storage --size {size} --format VDI --variant Standard'
vm_storage_detache = ''
vm_storage_remove = 'rm -rf {dir}{fs}_storage.vdi'
vm_storage_attach = 'VBoxManage storageattach {host}_{postfix} --storagectl "SATA Controller" --port 2 --device 0 --type hdd --medium {dir}{fs}_storage.vdi'
vm_power_on = 'VBoxManage startvm {host}_{postfix}'
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
            storage_host_client.run(run_storage_init.format(dir=script_dir,
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

    # Создать /report
    try:
        if not path.exists(REPORT_PATH):
            mkdir(REPORT_PATH, mode=0o755)
    except FileNotFoundError:
        pass

    try:
        with Connection(host='127.0.0.1',
                        port=HOSTS[args.HOST]['port'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as node_client:
            node_client.run(run_test.format(dir=script_dir,
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
        cmd(run_storage_init.format(dir=script_dir,
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

    # Создать /report
    if not path.exists(REPORT_PATH):
        mkdir(REPORT_PATH, mode=0o755)

    try:
        cmd(run_test.format(dir=script_dir,
                            ts=args.TS))
    except Exception as exception:
        print("\033[91m Тестирование завершилось исключением.\033[0m")
        print(exception)
        exit(2)

    cmd('umount {}'.format(STORAGE_MOUNT_DIR))
