import subprocess
import argparse
from sys import exit
from os import popen, path, mkdir
from time import sleep
from fabric import Connection
# from cfs_conf_2 import HOSTS
from libs.libcfs import create_remote_file, send_remote_command
from cfs_conf import STORAGE_NAME, REPORT_DIR, \
    STORAGE_MOUNT_DIR, USER, PASSWORD, SCRIPT_DIR

class CephStorageCreate():
    def __init__(self, astra_version, HOSTS, type_interface_ceph):
        self.astra_version = astra_version
        self.HOSTS = HOSTS
        self.type_interface_ceph = type_interface_ceph

    def create_storage(self):
        for host in self.HOSTS.keys():
            create_remote_file(local_file_path="storage_init", 
                               remote_file_path="/var/tmp/storage_init", 
                               ip=self.HOSTS[host]['ip'], 
                               user=self.HOSTS[host]['user'], 
                               password=self.HOSTS[host]['password'],
                               port=self.HOSTS[host]['port'])
            send_remote_command("sudo bash /var/tmp/storage_init/prep_ceph.sh", 
                                ip=self.HOSTS[host]['ip'], 
                                user=self.HOSTS[host]['user'],
                                password=self.HOSTS[host]['password'],
                                port=self.HOSTS[host]['port'])
            # send_remote_command("sudo bash /var/tmp/storage_init/sync_time.sh", 
            #                     ip=self.HOSTS[host]['ip'], 
            #                     user=self.HOSTS[host]['user'],
            #                     password=self.HOSTS[host]['password'],
            #                     port=self.HOSTS[host]['port'])
            

            
            ### TODO Синхронизация времени

        ### !!! РАЗВОРАЧИВАТЬ ОТ ПОЛЬЗОВАТЕЛЯ ceph-adm
        #### if astra_version == 1.7
        if self.astra_version.startswith("1.7"):
            send_remote_command(f"bash /var/tmp/storage_init/ceph_create_old.sh {self.type_interface_ceph}", ip=self.HOSTS['astra-ceph-admin']['ip'], user="ceph-adm", password="1", port=50020)
        elif self.astra_version.startswith("1.8"):
            # TODO забирать из hosts
            host_main = "10.0.5.31"
            # type_test = "rbd"
            send_remote_command(f"bash /var/tmp/storage_init/ceph_create_new.sh {host_main} {self.type_interface_ceph}", ip=self.HOSTS['testvm1']['ip'], user="ceph-adm", password="1", port=60001)
        else:
            return Exception(f"Не написан скрипт, разворачивающий ceph для версии {self.astra_version}")
        
if __name__ == "__main__":
    # hosts = {
    #     'testvm1': {
    #         'ip': "127.0.0.1",
    #         'user': "u",
    #         'password': "1",
    #         # 'port': 50020
    #         'port': 60001
    #         },
    #     'testvm2': {
    #         "ip": "127.0.0.1",
    #         "user": "u",
    #         "password": "1",
    #         "port": 60002
    #     },
    #     'testvm3': {
    #         "ip": "127.0.0.1",
    #         "user": "u",
    #         "password": "1",
    #         "port": 60003
    #     },
    #     'testvm4': {
    #         "ip": "127.0.0.1",
    #         "user": "u",
    #         "password": "1",
    #         "port": 60004
    #     },
    #     'testvm5': {
    #         "ip": "127.0.0.1",
    #         "user": "u",
    #         "password": "1",
    #         "port": 60005
    #     }
    # }
    hosts = {
        'astra-ceph-admin': {
            'ip': "127.0.0.1",
            'user': "u",
            'password': "1",
            'port': 50020
            # 'port': 60001
            },
        'astra-ceph1': {
            "ip": "127.0.0.1",
            "user": "u",
            "password": "1",
            # "port": 60002
            "port": 50011
        },
        'astra-ceph2': {
            "ip": "127.0.0.1",
            "user": "u",
            "password": "1",
            # "port": 60003
            "port": 50012
        },
        'astra-ceph3': {
            "ip": "127.0.0.1",
            "user": "u",
            "password": "1",
            # "port": 60004
            "port": 50013
        }
    }
    # for host in hosts.keys():
    #     print(hosts["astra-ceph-admin"]['ip'])
    #     # print(host)
    storage = CephStorageCreate(astra_version="1.7.7", HOSTS=hosts, type_interface_ceph="cephfs")
    storage.create_storage()