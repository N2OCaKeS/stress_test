# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess

from os import path, mkdir
from sys import exit
from threading import Thread
from fabric import Connection
from time import sleep, time
from libs.libtable import Report
from cfs_conf import MACHINE_POSTFIX, SNAPSHOT_NAME, \
    HOSTS, USER, PASSWORD, PORT, LOG_FILENAME, SCRIPT_DIR, REPORT_DIR, REPORT_FILENAME, INFO_FILENAME, \
    FILES, FILES_STEP, FILES_LIMIT, \
    SIZE, SIZE_STEP, SIZE_LIMIT, \
    START_BORDER_FOR_DATA, STEP_FOR_DATA, END_BORDER_FOR_DATA, IPTABLES_COMMAND

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('--virtual-box',
                    action='store_true',
                    required=False,
                    help='host type',
                    dest='VBOX')

parser.add_argument('--libvirt',
                    action='store_true',
                    required=False,
                    help='virtualization type',
                    dest='LIBVIRT')

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

parser.add_argument('--multithreading',
                    action='store_true',
                    required=False,
                    help='get data from config',
                    dest='MULTITHREADING')

parser.add_argument('--parsec',
                    action='store_true',
                    required=False,
                    help='',
                    dest='PARSEC')

args = parser.parse_args()
all_hosts = args.NODES + [args.STORAGE]

# bash cmd # /home/$USER/VirtualBox\ VMs/

if args.LIBVIRT:
    restore_snapshot = 'virsh --connect qemu:///system snapshot-revert {host}_{postfix} {snapshot}'
    storagecreate = 'cd /var/lib/libvirt/images && sudo qemu-img create -f qcow2 cluster_storage {size}M'
    storageattach = 'virsh --connect qemu:///system attach-device {host}_{postfix} --config storage.xml '
    startvm = 'virsh --connect qemu:///system start {host}_{postfix}'
    controlvm_off = 'virsh --connect qemu:///system destroy {host}_{postfix}'
else:
    restore_snapshot = 'VBoxManage snapshot {host}_{postfix} restore {shapshot}'
    storagecreate = 'VBoxManage createmedium disk --filename /home/$USER/VirtualBox\ VMs/{fs}_storage --size {size} --format VDI --variant Standard'
    storageattach = 'VBoxManage storageattach {host}_{postfix} --storagectl "SATA Controller" --port 2 --device 0 --type hdd --medium /home/$USER/VirtualBox\ VMs/{fs}_storage.vdi'
    startvm = 'VBoxManage startvm {host}_{postfix} --type headless'
    controlvm_off = 'VBoxManage controlvm {host}_{postfix} poweroff'

local_current_dir = path.dirname(path.realpath(__file__))

# Физ. машина
restore_snapshot_agb = ''
pm_on = ''
pm_off = ''

ssh_keygen = 'ssh-keygen -f "/home/$USER/.ssh/known_hosts" -R {ip}'
add_nodes_in_ssh_scrt = "sed -i '3s/.*/ips=({nodes} {host})/' /home/$USER/git/stress_test/cluster_file_system_benchmark/ssh_key.sh"
run_storage_init = 'sudo {dir}/venv/bin/python {dir}/cfs_storage_init.py --fs {fs} --host-storage {st_host} --nodes {hosts}'
run_test_cmd = 'sudo {dir}/venv/bin/python {dir}/{file} --test-set {ts}'


def host_is_available(node):
    try:
        if args.VBOX:  # вирт. стенд
            with Connection(host='127.0.0.1',
                            port=HOSTS[node]['port'],
                            user=USER,
                            connect_kwargs={"password": PASSWORD}) as node_client:
                if str(node_client.run('uptime')):
                    return True
        elif args.LIBVIRT: # libvirt
            with Connection(host=HOSTS[node]['ip'],
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


def run_test(node=args.NODES[0], test_set='fs_mark_count', cmd_template=run_test_cmd):

    if args.MULTITHREADING:
        test_cmd = cmd_template.format(dir=SCRIPT_DIR, file='cfs_th_test.py', ts=test_set)
    else:
        test_cmd = cmd_template.format(dir=SCRIPT_DIR, file='cfs_test.py', ts=test_set)

    if args.PARSEC:
        test_cmd = test_cmd + ' --parsec'

    try:
        if args.VBOX:  # вирт. стенд
            with Connection(host='127.0.0.1',
                            port=HOSTS[node]['port'],
                            user=USER,
                            connect_kwargs={"password": PASSWORD}) as node_client:
                node_client.run(test_cmd)

        elif args.LIBVIRT:
            with Connection(host=HOSTS[node]['ip'],
                            user=USER,
                            connect_kwargs={"password": PASSWORD}) as node_client:
                node_client.run(test_cmd)

        else:  # физ. стенд
            with Connection(host=HOSTS[node]['ip'],
                            user=USER,
                            connect_kwargs={"password": PASSWORD}) as node_client:
                node_client.run(test_cmd)
    except Exception as exception:
        print("\033[91m Тестирование завершилось исключением.\033[0m")
        print(exception)
        exit(2)


def shutdown_all_hosts(hosts=all_hosts):
    if args.VBOX: # вирт. стенд
        for host in hosts:
            subprocess.run(controlvm_off.format(host=host, postfix=MACHINE_POSTFIX),
                           shell=True,
                           stderr=subprocess.DEVNULL)
    elif args.LIBVIRT:
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


start_time = time()

if args.LIBVIRT:
    for command in IPTABLES_COMMAND:
        cmd(command)

if args.VBOX or args.LIBVIRT: # вирт. стенд
    # Восстановить последний актуальные снимки SNAPSHOT_NAME
    for host in all_hosts:
        sleep(1)
        cmd(restore_snapshot.format(host=host,
                                    postfix=MACHINE_POSTFIX,
                                    snapshot=SNAPSHOT_NAME))
    # Создать тестовый накопитель
    cmd(storagecreate.format(fs=args.FS,
                             size=args.DISK_SIZE))

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
if args.VBOX or args.LIBVIRT: # вирт. стенд
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
                 '        name = {hostname}\n'.format(hostname=HOSTS[node]['short_name']),
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
    if args.VBOX:
        with Connection(host='127.0.0.1',
                        port=HOSTS[args.STORAGE]['port'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as storage_host_client:
            storage_host_client.run(run_storage_init.format(dir=SCRIPT_DIR,
                                                            fs=args.FS,
                                                            st_host=args.STORAGE,
                                                            hosts=str_hosts))
    elif args.LIBVIRT:
        with Connection(host=HOSTS[args.STORAGE]['ip'],
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

# Создать /log
try:
    if not path.exists('log'):
        mkdir('log', mode=0o755)
except FileNotFoundError:
    pass

# Очистить лог
log_file = open(LOG_FILENAME, 'w')
log_file.close()

info_file = open(INFO_FILENAME, 'w')
info_file.close()

# Создать /report
try:
    if not path.exists('report'):
        mkdir('report', mode=0o755)
except FileNotFoundError:
    pass

# Очистить report
report_file = open('report/{}'.format(REPORT_FILENAME), 'w')
report_file.close()

if args.MULTITHREADING:
    threads = [Thread(target=run_test, args=(node, args.TS)) for node in args.NODES]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if args.TS == 'fs_mark_count':
        report = Report(ox_lo_lim=FILES,
                        ox_step=FILES_STEP,
                        ox_up_lim=FILES_LIMIT,
                        report='{}/report'.format(local_current_dir),
                        mtreading=True)
    elif args.TS == 'fs_mark_size':
        report = Report(ox_lo_lim=SIZE,
                        ox_step=SIZE_STEP,
                        ox_up_lim=SIZE_LIMIT,
                        report='{}/report'.format(local_current_dir),
                        mtreading=True)
    else:
        report = Report(ox_lo_lim=START_BORDER_FOR_DATA,
                        ox_step=STEP_FOR_DATA,
                        ox_up_lim=END_BORDER_FOR_DATA,
                        report='{}/report'.format(local_current_dir),
                        mtreading=True)

    report.create_beauty_table()
    report.create_cfs_fc_sp_graph()
    report.create_cfs_fc_app_overhead_graph()
    report.create_cfs_fc_create_graph()
    report.create_cfs_fc_write_graph()
    report.create_cfs_fc_fsync_graph()
    report.create_cfs_fc_sync_graph()
    report.create_cfs_fc_close_graph()
    report.create_cfs_fc_unlink_graph()
    report.create_tar(path_to_tar=local_current_dir)
else:
    run_test(test_set=args.TS)

print("#####################")
print("###### - END - ######")
print("#####################")

# Выключаем все машины
print('lead time: {t} sec'.format(t=time() - start_time))
shutdown_all_hosts()
