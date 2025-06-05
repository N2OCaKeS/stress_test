from src.utils.secondary_func import remote_cmd, remote_put_file
from src.main import get_all_stands

"""
    1) Установка пакетов kea-dhcp4-server, apache2, tftpd-hpa
    2) Настройка kea-dhcp4-server
    3) Замена AstraMode на off
    4) Создание директорий по ИМЕНИ СТЕНДА в /var/www/html/ и /srv/tftp/
    5) Копирование pxelinux.0 в /srv/tftp
"""

class ServerPresets:
    def __init__(self):
        self.host = ""
        self.user = ""
        self.passwd = ""

    def change_apache_settings(self):
        remote_cmd("sudo sed -i 's/# AstraMode on/AstraMode off/' /etc/apache2/apache2.conf && sudo systemctl restart apache2", host=self.host, user=self.user, passwd=self.passwd)

    def configuring_dhcp(self):
        remote_cmd("sudo rm -f /etc/kea/kea-dhcp4.conf", host=self.host, user=self.user, passwd=self.passwd)
        remote_put_file(remote_path="/etc/kea/kea-dhcp4.conf", local_path="kea-dhcp4.conf", host=self.host, user=self.user, passwd=self.passwd) 

    def create_directory_for_stands(self):
        ### TODO Забирать из БД ACS все стенды
        all_stands = get_all_stands(only_name=True)
        remote_cmd(f"cd /srv/tftp && sudo mkdir {all_stands}", host=self.host, user=self.user, passwd=self.passwd)
        remote_cmd(f"cd /var/www/html && sudo mkdir {all_stands}", host=self.host, user=self.user, passwd=self.passwd)

    def tune(self):
        remote_cmd("sudo apt install -y kea-dhcp4-server apache2 tftpd-hpa", host=self.host, user=self.user, passwd=self.passwd)
        self.change_apache_settings()
        self.configuring_dhcp()
        remote_cmd()
        remote_cmd("sudo apt install pxelinux && sudo cp /usr/lib/PXELINUX/pxelinux.0 /srv/tftp/", host=self.host, user=self.user, passwd=self.passwd)