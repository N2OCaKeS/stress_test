#### Ввод клиента в домен

from libs.libipa import cmd
from ipa_conf import DC_PASSWORD, DOMAIN, HOSTS, ASTRA_VERSION_CLIENTS

def init_ipa_client(qty, id_start, id_stop):
    # 1 Собрать докер образ из chroot и debootsrap с AL1.7.*
    # 2 Запуск докер контейнера с systemd в фоне уже с hostname==con_name и dns
    # 3 Установка ssh и astra-freeipa-client -y docker exec 
    # 4 Ввод клиента в домен astra-freeipa-client -y -p {dc_pass}
    """
        Запуск контейнеров в фоне
    """
    for id_client in range(id_start, id_start+qty):
        cmd(f"sudo docker run -d --name client_{id_client} -v /parsecfs:/parsecfs --privileged=true --dns {HOSTS['server']['ip']} --dns-search {DOMAIN} alse:{ASTRA_VERSION_CLIENTS} /usr/sbin/init")

    """
        Обновление репозиториев
    """
    for id_client in range(id_start, id_start+qty):
        cmd(f"sudo docker exec -it client_{id_client} apt update")

    """
        Установка пакетов
    """
    for id_client in range(id_start, id_start+qty):
        cmd(f"sudo docker exec -it client_{id_client} apt install ssh astra-freeipa-client -y")

    """
        Ввод клиентов в домен
    """
    for id_client in range(id_start, id_start+qty):
        cmd(f"sudo docker exec -it client_{id_client} astra-freeipa-client -y -p {DC_PASSWORD}")


def delete_ipa_client():
    """
        TODO Дописать удаление клиентов из домена
    """
    pass