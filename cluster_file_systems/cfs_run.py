# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

"""
    Start:
    1) sudo ./prepare.sh ветка версия
    2) Указать HOST_IP в cfs_conf.py
    3) /home/u/python/Python-3.12.1/venv/bin/python3 cfs_run.py --libvirt --fs ocfs2 --test-set fs_mark_count -vbox 1.8.0 -kernel 6.1.50-1-generic
"""

import argparse
import subprocess

from os import path, mkdir, getuid
from sys import exit
from threading import Thread
from fabric import Connection
from time import sleep, time
from libs.libtable import Report
from libs.libcfs import create_remote_file
from cfs_create_vms import VMS
from libs.zefir import UploaderZC

from cfs_conf import  \
    USER, PASSWORD, PORT, LOG_FILENAME, SCRIPT_DIR, REPORT_DIR, REPORT_FILENAME, INFO_FILENAME, \
    FILES, FILES_STEP, FILES_LIMIT, \
    SIZE, SIZE_STEP, SIZE_LIMIT, \
    START_BORDER_FOR_DATA, STEP_FOR_DATA, END_BORDER_FOR_DATA, \
    STORAGE_NAME, HOST_IP, HOST_STORAGE, NODES

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
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

parser.add_argument('--libvirt',
                    action='store_true',
                    required=False,
                    help='virtualization type',
                    dest='LIBVIRT')

parser.add_argument('--disk-size',
                    action='store',
                    required=False,
                    type=str,
                    default='25',
                    help='size of vdi disk',
                    dest='DISK_SIZE')

parser.add_argument('-fs',
                    action='store',
                    choices=['ocfs2', 'gfs2'],
                    required=True,
                    help='filesystem',
                    dest='FS')

parser.add_argument('--test-set',
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

parser.add_argument('-vbox', 
                    action='store',
                    required=True,
                    help='vbox name',
                    dest='VBOX')

parser.add_argument('-kernel', 
                    action='store',
                    required=True,
                    help='vbox name',
                    dest='KERNEL')


args = parser.parse_args()
args.STORAGE = HOST_STORAGE
args.NODES = NODES
all_hosts = args.NODES + [args.STORAGE]

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
                 file_system=args.FS,
                 test_set=args.TS)
    
uzs.upload_test_cycle_status('progress')

# bash cmd # /home/$USER/VirtualBox\ VMs/

if args.LIBVIRT:
    restore_snapshot = 'virsh --connect qemu:///system snapshot-revert {host} {snapshot}'
    storagecreate = 'cd /var/lib/libvirt/images && sudo qemu-img create -f qcow2 cluster_storage {size}G'
    # storageattach = 'virsh --connect qemu:///system attach-device {host} --config storage.xml '
    storageattach = f"virsh --connect qemu:///system attach-disk testvm1 --source /var/lib/libvirt/images/cluster_storage --target {STORAGE_NAME} --persistent --driver qemu --subdriver qcow2 --type disk"
    startvm = 'virsh --connect qemu:///system start {host}'
    controlvm_off = 'virsh --connect qemu:///system destroy {host}'
# else:
#     restore_snapshot = 'VBoxManage snapshot {host}_{postfix} restore {snapshot}'
#     storagecreate = 'VBoxManage createmedium disk --filename /home/$USER/VirtualBox\ VMs/{fs}_storage --size {size} --format VDI --variant Standard'
#     storageattach = 'VBoxManage storageattach {host}_{postfix} --storagectl "SATA Controller" --port 2 --device 0 --type hdd --medium /home/$USER/VirtualBox\ VMs/{fs}_storage.vdi'
#     startvm = 'VBoxManage startvm {host}_{postfix} --type headless'
#     controlvm_off = 'VBoxManage controlvm {host}_{postfix} poweroff'

local_current_dir = path.dirname(path.realpath(__file__))

# Физ. машина
restore_snapshot_agb = ''
pm_on = ''
pm_off = ''

# Получите uid текущего пользователя
user_id = getuid()

if user_id == 0:
    ssh_keygen = 'ssh-keygen -f "/root/.ssh/known_hosts" -R {ip}'
else:
    ssh_keygen = 'ssh-keygen -f "/home/$USER/.ssh/known_hosts" -R {ip}'
add_nodes_in_ssh_scrt = "sed -i '3s/.*/ips=({nodes} {host})/' /home/u/git/stress_test/cluster_file_systems/ssh_key.sh"
run_storage_init = 'sudo python3 {dir}/cfs_storage_init.py --fs {fs} --host-storage {st_host} --nodes {hosts}'
run_test_cmd = 'sudo python3 {dir}/{file} --test-set {ts}'


def host_is_available(node):
    try:
        # if args.VBOX:  # вирт. стенд
        #     with Connection(host='127.0.0.1',
        #                     port=HOSTS[node]['port'],
        #                     user=USER,
        #                     connect_kwargs={"password": PASSWORD}) as node_client:
        #         if str(node_client.run('uptime')):
        #             return True
        if args.LIBVIRT: # libvirt
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
        # if args.VBOX:  # вирт. стенд
        #     with Connection(host='127.0.0.1',
        #                     port=HOSTS[node]['port'],
        #                     user=USER,
        #                     connect_kwargs={"password": PASSWORD}) as node_client:
        #         node_client.run(test_cmd)

        if args.LIBVIRT:
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
    # if args.VBOX: # вирт. стенд
    #     for host in hosts:
    #         subprocess.run(controlvm_off.format(host=host, postfix=MACHINE_POSTFIX),
    #                        shell=True,
    #                        stderr=subprocess.DEVNULL)
    if args.LIBVIRT:
        for host in hosts:
            subprocess.run(controlvm_off.format(host=host),
                           shell=True,
                           stderr=subprocess.DEVNULL)

    else:  # физ. стенд
        for host in hosts:
            subprocess.run(pm_off.format(host=host),
                           shell=True,
                           stderr=subprocess.DEVNULL)


def cmd(command):
    subprocess.run(command,
                   shell=True,
                   stderr=subprocess.STDOUT)


start_time = time()

# if args.LIBVIRT:
#     for command in IPTABLES_COMMAND:
#         cmd(command)



if args.VBOX or args.LIBVIRT: # вирт. стенд
#     # Восстановить последний актуальные снимки SNAPSHOT_NAME
#     for host in all_hosts:
#         sleep(1)
#         cmd(restore_snapshot.format(host=host, 
#                                     snapshot=SNAPSHOT_NAME))
    # Создать тестовый накопитель

    """
        TODO 
        1) Подсчет node + 1 storage init и передать в vm_count
        2) Передача rc_vbox и аргуменгта при запуске
        3) Передача hostip например из ip или из конфига
    """
    virt_machines = VMS(rc_vbox=args.VBOX, vm_count=len(all_hosts), hostip=HOST_IP, kernel=args.KERNEL)
    virt_machines.prepare_and_start()

    cmd("sudo firewall-cmd --permanent --zone=libvirt --add-service=nfs")
    cmd("sudo firewall-cmd --permanent --zone=libvirt --add-service=mountd")
    cmd("sudo firewall-cmd --permanent --zone=libvirt --add-service=rpc-bind")
    cmd("sudo firewall-cmd --reload")

    cmd(storagecreate.format(fs=args.FS,
                             size=args.DISK_SIZE))

    
    cmd(storageattach.format(host=args.STORAGE,
                             fs=args.FS))


    HOSTS = virt_machines.vm_dates
    # testvm1_value = HOSTS.pop('testvm1')
    # HOSTS.update({'testvm1': testvm1_value})
    # print(HOSTS)

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
        cmd(startvm.format(host=host))   

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
                 '        name = {hostname}\n'.format(hostname=node),
                 '\n']
    conf += node_conf

with open('cluster.conf', 'w') as file:
    file.writelines(conf)

str_hosts = " ".join(args.NODES)

print("TEST")
# Настройка storage машины
print("#########################")
print("###### - PREPAPE - ######")
print("#########################")
try:
    # if args.VBOX:
    #     with Connection(host='127.0.0.1',
    #                     port=HOSTS[args.STORAGE]['port'],
    #                     user=USER,
    #                     connect_kwargs={"password": PASSWORD}) as storage_host_client:
    #         storage_host_client.run(run_storage_init.format(dir=SCRIPT_DIR,
    #                                                         fs=args.FS,
    #                                                         st_host=args.STORAGE,
    #
    #                                                          hosts=str_hosts))
    with open("./cfs_conf_2.py", "w+") as file:
        hosts_str = f"HOSTS = {HOSTS}"
        file.write(hosts_str)
    
    # exit(1)

    if args.LIBVIRT:

        for node in all_hosts:
             with Connection(host=HOSTS[node]['ip'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as storage_host_client:
                storage_host_client.run("sudo mount {hostip}:/home/u/git/stress_test/cluster_file_systems {dir}".format(dir=SCRIPT_DIR,
                                                                                                                                 hostip=HOST_IP))

        with Connection(host=HOSTS[args.STORAGE]['ip'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as storage_host_client:
            print("test_prep")
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
    print("$$$$$$")
    print(exptn)
    print("$$$$")
    # exit(2)

# Ожидание окончания перезагрузки машин для подключения хранилища

# if args.LIBVIRT:
#         with Connection(host=HOSTS[args.STORAGE]['ip'],
#                         user=USER,
#                         connect_kwargs={"password": PASSWORD}) as storage_host_client:
#             storage_host_client.run("sudo reboot")

for node in args.NODES:
    while host_is_available(node) is False:
        sleep(1)
    sleep(10)

# # exit(1)
if args.LIBVIRT:
        with Connection(host=HOSTS[args.NODES[0]]['ip'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as storage_host_client:
            storage_host_client.run("sudo mount {hostip}:/home/u/git/stress_test/cluster_file_systems {dir}".format(dir=SCRIPT_DIR,
                                                                                                                             hostip=HOST_IP))


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



uzs.public = True
uzs.statistics = True
uzs.upload_test_cycle_status(zefir_status='pass')
