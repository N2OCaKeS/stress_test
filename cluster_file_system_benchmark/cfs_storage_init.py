# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import subprocess
import argparse
from sys import exit
from os import popen
from time import sleep
from fabric import Connection
from cfs_conf import STORAGE_NAME, HOSTS, INODE_COUNT, \
    STORAGE_MOUNT_DIR, USER, PASSWORD, SCRIPT_DIR

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
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

args = parser.parse_args()


def cmd(command,
        good_color='\033[92m',
        mid_color='\033[93m',
        bad_color='\033[91m',
        def_color='\033[0m'):

    code = subprocess.run(command, shell=True, stderr=subprocess.DEVNULL).returncode
    if code == 0 or code == 127:
        print('{}# +++ {}{}'.format(good_color, command, def_color))
    elif code == 1:
        print('{}# +-+ {}{}'.format(mid_color, command, def_color))
        #exit(1)
    else:
        print('{}# --- {}{}'.format(bad_color, command, def_color))
        #exit(2)


def host_is_available(node):
    try:
        with Connection(host=HOSTS[node]['ip'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as node_client:
            if str(node_client.run('uptime')):
                return True
    except Exception:
        return False


if args.FS == 'ocfs2':
    # Проброс ssh key
    cmd('sudo {}/ssh_key.sh'.format(SCRIPT_DIR))
    # Установка пакета
    cmd('apt-get install -y targetcli-fb ocfs2-tools')
    # Проверка наличия диска
    cmd('lsblk | grep sdb')

    # Создание блока памяти
    cmd('targetcli /backstores/block create storage01 /dev/{device}'.format(device=STORAGE_NAME))
    # Создать таргет
    cmd('targetcli /iscsi create')

    # Установить параметры авторизации
    iscsi_iqn = popen('targetcli ls /iscsi | grep iqn | cut -d" " -f4').read().strip()
    # Установить параметры авторизации для остальных машин
    node_iqn_dict = {}
    index = 0
    for node in args.NODES:  # словарь переменная_N -> значение
        index += 1
        cmd('ssh {node_ip} sudo apt-get install -y open-iscsi bridge-utils ocfs2-tools'.format(node_ip=HOSTS[node]['ip']))
        node_iqn_dict['node{}_iqn'.format(index)] = popen(
            'ssh {node_ip} sudo cat /etc/iscsi/initiatorname.iscsi | grep -v "##" | cut -d "=" -f2'.format(
                node_ip=HOSTS[node]['ip'])).read().strip()

    cmd('targetcli /iscsi/{iqn}/dtpg1 set parameter AuthMethod=None'.format(iqn=iscsi_iqn))
    sleep(1)
    cmd('targetcli /iscsi/{iqn}/tpg1 set attribute authentication=0'.format(iqn=iscsi_iqn))
    sleep(1)

    # Отключить контроль доступа
    cmd('targetcli /iscsi/{iqn}/tpg1 set attribute generate_node_acls = 1'.format(iqn=iscsi_iqn))
    sleep(1)
    cmd('targetcli /iscsi/{iqn}/tpg1 set attribute demo_mode_write_protect = 0'.format(iqn=iscsi_iqn))
    sleep(1)

    # for nodeN_iqn in node_iqn_dict.keys():  # key = nodeN_iqn
    #     cmd('targetcli /iscsi/{iqn}/tpg1/acls create {node_iqn}'.format(iqn=iscsi_iqn,
    #                                                                     node_iqn=node_iqn_dict[nodeN_iqn]))
    #     sleep(1)

    # Создать LUNs
    cmd('targetcli /iscsi/{iqn}/tpg1/luns create /backstores/block/storage01'.format(iqn=iscsi_iqn))

    # Сохранить настройки
    cmd('targetcli / saveconfig')

    # Установка ISCSI-initiator
    # Поиск LUNs
    for node in args.NODES:
        cmd('ssh {node_ip} "sudo iscsiadm -m discovery -t st -p {storage_ip}"'.format(node_ip=HOSTS[node]['ip'],
                                                                                      storage_ip=HOSTS[args.STORAGE]['ip']))

    # Автоподключение LUNs
    for node in args.NODES:
        cmd('ssh {node_ip} "sudo iscsiadm -m node -p {storage_ip} -l"'.format(node_ip=HOSTS[node]['ip'],
                                                                              storage_ip=HOSTS[args.STORAGE]['ip']))
        sleep(1)
        cmd('ssh {node_ip} "sudo iscsiadm -m node -p {storage_ip} -o update -n node.startup -v automatic"'
            .format(node_ip=HOSTS[node]['ip'],
                    storage_ip=HOSTS[args.STORAGE]['ip']))

    # Проверка автоподключение LUNs
    cmd1 = 'ssh {node_ip} "sudo cat /etc/iscsi/nodes/{iqn}/{storage_ip}\,3260\,1/default | grep node.startup | grep automatic"'
    for node in args.NODES:
        cmd(cmd1.format(node_ip=HOSTS[node]['ip'],
                        iqn=iscsi_iqn,
                        storage_ip=HOSTS[args.STORAGE]['ip']))

    # Установить файловую систему ocfs2
    for node in args.NODES:
        cmd('ssh {node_ip} "sudo cp /media/sf_git/stress_test/cluster_file_system_benchmark/cluster.conf /etc/ocfs2/cluster.conf"'.format(node_ip=HOSTS[node]['ip']))
        cmd('ssh {node_ip} "sudo sed -i "s/false/true/" /etc/default/o2cb"'.format(node_ip=HOSTS[node]['ip']))
        cmd('ssh {node_ip} "sudo dpkg-reconfigure ocfs2-tools -f noninteractive"'.format(node_ip=HOSTS[node]['ip']))
        cmd('ssh {node_ip} "sudo systemctl restart o2cb"'.format(node_ip=HOSTS[node]['ip']))

    # Проверка конфига
    for node in args.NODES:
        cmd('ssh {node_ip} "sudo debconf-show ocfs2-tools | grep init | grep true"'.format(node_ip=HOSTS[node]['ip']))

    # Добавить unit восстановления targetcli
    unit = ["[unit]\n",
            "Description=Restore LIO kernel target configuration\n",
            "Requires=sys-kernel-config.mount\n",
            "After=sys-kernel-config.mount network.target local-fs.target\n",
            "[Service]\n",
            "Type=oneshot\n",
            "RemainAfterExit=yes\n",
            "ExecStart=/usr/bin/targetctl restore\n",
            "ExecStop=/usr/bin/targetctl clear\n",
            "SyslogIdentifier=target\n",
            "[Install]\n",
            "WantedBy=multi-user.target\n"]
    with open('/lib/systemd/system/target.service', 'w') as service_file:
        service_file.writelines(unit)
    cmd('systemctl daemon-reload')
    sleep(1)
    cmd('systemctl enable target')

    # Форматировать LUNs в ocfs2
    cmd('parted -s /dev/{device} mklabel gpt mkpart primary ntfs 0% 100%'.format(device=STORAGE_NAME))

    # Перезагрузить ноды
    for node in args.NODES:
        cmd('ssh {node_ip} "sudo reboot"'.format(node_ip=HOSTS[node]['ip']))

    # Ожидание окончания перезагрузки машин для подключения хранилища
    for node in args.NODES:
        while host_is_available(node) is False:
            sleep(1)
        sleep(10)

    cmd('ssh {node_ip} "sudo mkfs.ocfs2 -F --cluster-stack=o2cb --cluster-name=ocfs2 /dev/{device}1"'.format(node_ip=HOSTS[args.NODES[0]]['ip'],
                                                                                                             device=STORAGE_NAME))

    # Сделать запись в /etc/fstab
    sd_uuid = popen("blkid -o list | grep sdb1 | awk '{print $NF}'").read().strip()

    for node in args.NODES:
        cmd('ssh {node_ip} "sudo bash -c \'echo -e dlm >> /etc/modules-load.d/modules.conf\'"'.format(node_ip=HOSTS[node]['ip']))
        cmd('ssh {node_ip} "sudo echo -e \"UUID={uuid}\t{mount_dir}\tocfs2\t_netdev,defaults\t0\t0\" >> /etc/fstab"'.format(node_ip=HOSTS[node]['ip'],
                                                                                                                            uuid=sd_uuid,
                                                                                                                            mount_dir=STORAGE_MOUNT_DIR))
        subprocess.run('ssh {node_ip} "sudo reboot"'.format(node_ip=HOSTS[node]['ip']), shell=True)