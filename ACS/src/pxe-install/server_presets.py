from src.utils.secondary_func import remote_cmd, remote_put_file

"""
    1) Установка пакетов kea-dhcp4-server, apache2, tftpd-hpa
    2) Настройка kea-dhcp4-server
    3) Замена AstraMode на off
    4) Создание директорий по ИМЕНИ СТЕНДА в /var/www/html/ и /srv/tftp/
    5) Копирование pxelinux.0 в /srv/tftp
"""

def change_apache_settings():
    remote_cmd("sudo sed -i 's/# AstraMode on/AstraMode off/' /etc/apache2/apache2.conf && sudo systemctl restart apache2", host=..., user=..., passwd=...)

def configuring_dhcp():
    remote_cmd("sudo rm -f /etc/kea/kea-dhcp4.conf", host=..., user=..., passwd=...)
    remote_put_file(remote_path="/etc/kea/kea-dhcp4.conf", local_path="kea-dhcp4.conf", host=..., user=..., passwd=...) 

def presets():
    remote_cmd("sudo apt install -y kea-dhcp4-server apache2 tftpd-hpa", host=..., user=..., passwd=...)
    change_apache_settings()
    configuring_dhcp()
    remote_cmd()
    remote_cmd("sudo apt install pxelinux && sudo cp /usr/lib/PXELINUX/pxelinux.0 /srv/tftp/")