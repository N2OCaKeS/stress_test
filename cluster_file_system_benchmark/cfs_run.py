# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess
import concurrent.futures

from sys import exit
from fabric import Connection
from time import sleep
from cfs_conf import MACHINE_POSTFIX, SNAPSHOT_NAME, \
    HOSTS, USER, PASSWORD, PORT, LOG_FILENAME, SCRIPT_DIR

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
                    choices=['ocfs2', 'gfs2'],
                    required=True,
                    help='filesystem',
                    dest='FS')

parser.add_argument('--host-storage',
                    action='store',
                    required=True,
                    help='hostname where the storage is located',
                    dest='STORAGE')

parser.add_argument('--nodes',
                    action='store',
                    nargs="+",
                    required=True,
                    help='nodes list <hostname1 hostname2 hostname3 ...>',
                    dest='NODES')

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

parser.add_argument('--thread-variant',
                    action='store',
                    choices=['files',
                             'symlinks',
                             'hardlinks',
                             'archs',
                             'isos'],
                    required=False,
                    help='type of file for multihost threads test',
                    dest='VARIANT')

args = parser.parse_args()
all_hosts = args.NODES + [args.STORAGE]

#

# bash cmd # /home/$USER/VirtualBox\ VMs/
restore_snapshot = 'VBoxManage snapshot {host}_{postfix} restore {shapshot}'
storagecreate = 'VBoxManage createmedium disk --filename /home/$USER/VirtualBox\ VMs/{fs}_storage --size {size} --format VDI --variant Standard'
storageattach = 'VBoxManage storageattach {host}_{postfix} --storagectl "SATA Controller" --port 2 --device 0 --type hdd --medium /home/$USER/VirtualBox\ VMs/{fs}_storage.vdi'
startvm = 'VBoxManage startvm {host}_{postfix}'
controlvm_off = 'VBoxManage controlvm {host}_{postfix} poweroff'

# Физ. машина
restore_snapshot_agb = ''
pm_on = ''
pm_off = ''

ssh_keygen = 'ssh-keygen -f "/home/$USER/.ssh/known_hosts" -R {ip}'
add_nodes_in_ssh_scrt = "sed -i '3s/.*/ips=({nodes} {host})/' /home/$USER/git/stress_test/cluster_file_system_benchmark/ssh_key.sh"
run_storage_init = 'sudo {dir}/venv/bin/python {dir}/cfs_storage_init.py --fs {fs} --host-storage {st_host} --nodes {hosts}'
run_single_test = 'sudo {dir}/venv/bin/python {dir}/cfs_test.py --test-set {ts}'
run_th_test = 'sudo {dir}/venv/bin/python {dir}/cfs_th_test.py -v {variant}'


def host_is_available(node):
    try:
        if args.VIRTUAL:  # вирт. стенд
            with Connection(host='127.0.0.1',
                            port=HOSTS[node]['port'],
                            user=USER,
                            connect_kwargs={"password": PASSWORD}) as node_client:
                if str(node_client.run('uptime')):
                    return True
        else:  # физ. стенд
            with Connection(host=HOSTS[node]['ip'],
                            user=USER,
                            connect_kwargs={"password": PASSWORD}) as node_client:
                if str(node_client.run('uptime')):
                    return True
    except Exception:
        return False


def run_thread_test(node, v):
    try:
        if args.VIRTUAL:  # вирт. стенд
            with Connection(host='127.0.0.1',
                            port=HOSTS[node]['port'],
                            user=USER,
                            connect_kwargs={"password": PASSWORD}) as node_client:
                node_client.run(run_th_test.format(dir=SCRIPT_DIR, variant=v))
        else:  # физ. стенд
            with Connection(host=HOSTS[node]['ip'],
                            user=USER,
                            connect_kwargs={"password": PASSWORD}) as node_client:
                node_client.run(run_th_test.format(dir=SCRIPT_DIR, variant=v))
    except Exception as exptn:
       print("\033[91m Тестирование завершилось исключением.\033[0m")
       print(exptn)
       exit(2)


def shutdown_all_hosts(hosts=all_hosts):
    if args.VIRTUAL: # вирт. стенд
        for host in hosts:
            subprocess.run(controlvm_off.format(host=host, postfix=MACHINE_POSTFIX),
                           shell=True,
                           stderr=subprocess.DEVNULL)
    else:  # физ. стенд
        for host in hosts:
            subprocess.run(pm_off.format(host=host, postfix=MACHINE_POSTFIX),
                           shell=True,
                           stderr=subprocess.DEVNULL)


def cmd(command):
    subprocess.run(command,
                   shell=True,
                   stderr=subprocess.DEVNULL)


if args.VIRTUAL: # вирт. стенд
    # Восстановить последний актуальные снимки SNAPSHOT_NAME
    for host in all_hosts:
        sleep(1)
        cmd(restore_snapshot.format(host=host,
                                    postfix=MACHINE_POSTFIX,
                                    shapshot=SNAPSHOT_NAME))
    # Создать тестовый накопитель
    cmd(storagecreate.format(fs=args.FS,
                             size=args.DISK_SIZE))

    # Подключить тестовый накопитель
    cmd(storageattach.format(host=args.STORAGE,
                             postfix=MACHINE_POSTFIX,
                             fs=args.FS))
else:  # физ. стенд
    for host in all_hosts:
        while host_is_available(host) is False:
            sleep(1)
        sleep(10)

# Генерим ключи для ssh
for host in all_hosts:
    cmd(ssh_keygen.format(ip=HOSTS[host]['ip']))

# Запустить машины
if args.VIRTUAL: # вирт. стенд
    for host in all_hosts:
        sleep(3)
        cmd(startvm.format(host=host, postfix=MACHINE_POSTFIX))
else:  # физ. стенд
    pass

# Дождаться окончания загрузки
for host in all_hosts:
    while host_is_available(host) is False:
        sleep(1)
    sleep(10)

# Добавить список машин в ssh_key.sh
ips = ["'"+HOSTS[node]['ip']+"'" for node in args.NODES]
cmd(add_nodes_in_ssh_scrt.format(nodes=' '.join(ips),
                                 host="'"+HOSTS[args.STORAGE]['ip']+"'",
                                 dir=SCRIPT_DIR))

# Сгенерировать cluster.conf
conf = ['cluster:\n',
        '        heartbeat_mode = local\n',
        '        node_count = {count}\n'.format(count=len(args.NODES)),
        '        name = {fs}\n'.format(fs=args.FS),
        '\n']

index = -1
for node in args.NODES:
    index += 1
    node_conf = ['node:\n',
                 '        number = {i}\n'.format(i=index),
                 '        cluster = {fs}\n'.format(fs=args.FS),
                 '        ip_port = {p}\n'.format(p=PORT),
                 '        ip_address = {ip}\n'.format(ip=HOSTS[node]['ip']),
                 '        name = {hostname}\n'.format(hostname=HOSTS[node]['full_name']),
                 '\n']
    conf += node_conf

with open('cluster.conf', 'w') as file:
    file.writelines(conf)

str_hosts = " ".join(args.NODES)
# Настройка storage машины
print("#########################")
print("###### - PREPAPE - ######")
print("#########################")
try:
    if args.VIRTUAL:  # вирт. стенд
        with Connection(host='127.0.0.1',
                        port=HOSTS[args.STORAGE]['port'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as storage_host_client:
            storage_host_client.run(run_storage_init.format(dir=SCRIPT_DIR,
                                                            fs=args.FS,
                                                            st_host=args.STORAGE,
                                                            hosts=str_hosts))
    else:  # физ. стенд
        with Connection(host=HOSTS[args.STORAGE]['ip'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as storage_host_client:
            storage_host_client.run(run_storage_init.format(dir=SCRIPT_DIR,
                                                            fs=args.FS,
                                                            st_host=args.STORAGE,
                                                            hosts=str_hosts))
except Exception as exptn:
    print("\033[91mНе удалось настроить стенд.\033[0m")
    print(exptn)
    exit(2)

# Ожидание окончания перезагрузки машин для подключения хранилища
for node in args.NODES:
    while host_is_available(node) is False:
        sleep(1)
    sleep(10)

print("#########################")
print("###### - TESTING - ######")
print("#########################")

# Очистить лог
cmd('rm -f {}'.format(LOG_FILENAME))

# На одной машине
try:
    if args.VIRTUAL:  # вирт. стенд
        with Connection(host='127.0.0.1',
                        port=HOSTS[args.NODES[0]]['port'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as node_client:
            node_client.run(run_single_test.format(dir=SCRIPT_DIR, ts=args.TS))
    else:  # физ. стенд
        with Connection(host=HOSTS[args.NODES[0]]['ip'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as node_client:
            node_client.run(run_single_test.format(dir=SCRIPT_DIR, ts=args.TS))
except Exception as exception:
    print("\033[91m Тестирование завершилось исключением.\033[0m")
    print(exception)
    exit(2)

# На нескольких машинах параллельно
# with concurrent.futures.ThreadPoolExecutor(max_workers=len(args.NODES)) as executor:
#     executor.map(run_thread_test.format(args.VARIANT), args.NODES, range(1, len(args.NODES)+1))

print("#####################")
print("###### - END - ######")
print("#####################")

# Выключаем все машины
#shutdown_all_hosts()
