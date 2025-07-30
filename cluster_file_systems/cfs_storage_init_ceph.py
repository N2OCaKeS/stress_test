import subprocess
import argparse
from sys import exit
from os import popen, path, mkdir
from time import sleep
from fabric import Connection
from cfs_conf_2 import HOSTS
from libs.libcfs import create_remote_file, send_remote_command
from cfs_conf import STORAGE_NAME, REPORT_DIR, \
    STORAGE_MOUNT_DIR, USER, PASSWORD, SCRIPT_DIR

class CephStorageCreate():
    def __init__(self, astra_version):
        self.astra_version = astra_version

    def create_storage(self):
        for host in HOSTS.keys():
            create_remote_file(local_file_path="storage_init", 
                               remote_file_path="home/u/storage_init", 
                               ip=HOSTS[host]['ip'], 
                               user=HOSTS[host]['user'], 
                               password=HOSTS[host]['password'],)
            send_remote_command("sudo bash prep_ceph.sh", 
                                ip=HOSTS[host]['ip'], 
                                user=HOSTS[host]['user'],
                                password=HOSTS[host]['password'],)
            ### TODO Синхронизация времени

        ### !!! РАЗВОРАЧИВАТЬ ОТ ПОЛЬЗОВАТЕЛЯ ceph-adm
        #### if astra_version == 1.7
        if self.astra_version.startwith("1.7"):
            send_remote_command("sudo -u ceph-adm bash storage_init/ceph_create_old.sh", ip=HOSTS['testvm1']['ip'], user="ceph-adm", password="1")
        elif self.astra_version.startwith("1.8"):
            send_remote_command("sudo bash storage_init/sync_time.sh")
            send_remote_command("sudo -u ceph-adm bash storage_init/ceph_create_new.sh", ip=HOSTS['testvm1']['ip'], user="ceph-adm", password="1")
        else:
            return Exception(f"Не написан скрипт, разворачивающий ceph для версии {self.astra_version}")