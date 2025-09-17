from libs.libcfs import create_remote_file, send_remote_command


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
                               user=self.HOSTS[host]['login'], 
                               password=self.HOSTS[host]['password'])
            send_remote_command("sudo bash /var/tmp/storage_init/prep_ceph.sh", 
                                ip=self.HOSTS[host]['ip'], 
                                user=self.HOSTS[host]['login'],
                                password=self.HOSTS[host]['password'])
            send_remote_command("sudo bash /var/tmp/storage_init/sync_time.sh", 
                                ip=self.HOSTS[host]['ip'], 
                                user=self.HOSTS[host]['login'],
                                password=self.HOSTS[host]['password'])
        
        if self.astra_version.startswith("1.7"):
            send_remote_command(f"bash /var/tmp/storage_init/ceph_create_old.sh {self.type_interface_ceph}", ip=self.HOSTS['testvm1']['ip'], user="ceph-adm", password="1")
        elif self.astra_version.startswith("1.8"):
            send_remote_command(f"bash /var/tmp/storage_init/ceph_create_new.sh {self.HOSTS['testvm1']['ip']} {self.type_interface_ceph}", ip=self.HOSTS['testvm1']['ip'], user="ceph-adm", password="1")
        else:
            return Exception(f"Не написан скрипт, разворачивающий ceph для версии {self.astra_version}")