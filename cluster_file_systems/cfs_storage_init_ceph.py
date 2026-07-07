from libs.libcfs import create_remote_file, send_remote_command


class CephStorageCreate():
    def __init__(self, astra_version, HOSTS, type_interface_ceph):
        self.astra_version = astra_version
        self.HOSTS = HOSTS
        self.type_interface_ceph = type_interface_ceph

    def check_ceph_mounted(self):
        output = send_remote_command("df -h | grep -E '[[:space:]]/mnt$'",
                                     ip=self.HOSTS['testvm1']['ip'],
                                     user="ceph-adm",
                                     password="1")
        return output.strip() != ""

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
        
        status = True

        if self.astra_version.startswith("1.7"):
            send_remote_command(f"bash /var/tmp/storage_init/ceph_create_old.sh {self.type_interface_ceph}", ip=self.HOSTS['testvm1']['ip'], user="ceph-adm", password="1")
            status = self.check_ceph_mounted()
        elif self.astra_version.startswith("1.8"):
            send_remote_command(f"bash /var/tmp/storage_init/ceph_create_new.sh {self.HOSTS['testvm1']['ip']} {self.type_interface_ceph}", ip=self.HOSTS['testvm1']['ip'], user="ceph-adm", password="1")
            status = self.check_ceph_mounted()
        else:
            status = False
        
        print(f"*****STATUS = {status}*****")
        with open("ceph_storage_status.txt", "w") as f:
            f.write(str(status))
        return status