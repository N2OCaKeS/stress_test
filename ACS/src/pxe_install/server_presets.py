from src.utils.secondary_func import remote_cmd, remote_put_file
# from src.main import get_all_stands

"""
    1) Установка пакетов kea-dhcp4-server, apache2, tftpd-hpa grub-efi
    2) Настройка kea-dhcp4-server
    3) Замена AstraMode на off
    4) Создание директорий по ИМЕНИ СТЕНДА в /var/www/html/ и /srv/tftp/
    5) Копирование pxelinux.0 в /srv/tftp
"""

class ServerPresets:
    def __init__(self):
        self.host = "host.docker.internal"
        self.user = "u"
        self.passwd = "1"
        self.port = 20022
    
    def p_env(self):
        remote_cmd("sudo apt install -y python3-pip && sudo pip3 install beautifulsoup4 && sudo pip3 install requests")

    def change_apache_settings(self):
        remote_cmd("sudo sed -i 's/# AstraMode on/AstraMode off/' /etc/apache2/apache2.conf && sudo systemctl restart apache2", host=self.host, user=self.user, passwd=self.passwd, port=self.port)

    def configuring_dhcp(self):
        remote_cmd("sudo rm -f /etc/kea/kea-dhcp4.conf", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        remote_cmd("sudo chown u:u /etc/kea", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        ###### TODO TODO TODO !!!!! убрать - test на боевом из local_path
        remote_put_file(remote_path="/etc/kea/kea-dhcp4.conf", local_path="/fastapi_app/src/pxe_install/kea-dhcp4-test.conf", host=self.host, user=self.user, passwd=self.passwd, port=self.port) 
        remote_cmd("sudo systemctl restart kea-dhcp4-server", host=self.host, user=self.user, passwd=self.passwd, port=self.port)

    def create_directory_for_stands(self):
        ### TODO Забирать из БД ACS все стенды
        # all_stands = get_all_stands(only_name=True)
        all_stands = "LowServer MiddleServer"
        remote_cmd(f"cd /srv/tftp && sudo mkdir {all_stands}", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        remote_cmd(f"cd /var/www/html && sudo mkdir {all_stands}", host=self.host, user=self.user, passwd=self.passwd, port=self.port)

    def tune(self):
        remote_cmd("sudo apt install -y kea-dhcp4-server apache2 tftpd-hpa pxelinux grub-efi", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        self.p_env()
        self.change_apache_settings()
        self.configuring_dhcp()
        self.create_directory_for_stands()
        # remote_cmd()
        remote_cmd("sudo cp /usr/lib/PXELINUX/pxelinux.0 /srv/tftp/", host=self.host, user=self.user, passwd=self.passwd, port=self.port)