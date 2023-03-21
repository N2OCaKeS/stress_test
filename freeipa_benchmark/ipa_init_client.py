#### Ввод клиента в домен

from time import sleep
from libs.libipa import cmd, remote_cmd, remote_exec
from ipa_conf import DC_PASSWORD, DOMAIN, HOSTS, ASTRA_VERSION_CLIENTS


def create_centos_cont(qty_clients, id_start=1, id_stop=2):
    """
        Запуск контейнеров в фоне
    """
    for id_client in range(id_start, id_start+qty_clients):
        # remote_cmd(f"sudo docker run -d --name client_{id_client} -h client{id_client}.{DOMAIN} -v /parsecfs:/parsecfs -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run --privileged=true --dns {HOSTS['server']['ip']} --dns-search {DOMAIN} alse:{ASTRA_VERSION_CLIENTS} /usr/sbin/init", HOSTS['client']['ip'])
        # remote_exec(f"sudo docker run -d --name client_{id_client} -h client{id_client} --privileged=true centos /usr/sbin/init", 'client')
        remote_cmd(f"sudo docker run -tid --name client_{id_client} -h client{id_client}.{DOMAIN} --dns {HOSTS['server']['ip']} --dns-search {DOMAIN} -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run local/c7-systemd-freeipa-client", HOSTS['client']['ip'])
        # sleep(10)

def update_repo_in_docker_conts(qty_clients, id_start=1, id_stop=2):
    """
        Обновление репозиториев
    """
    for id_client in range(id_start, id_start+qty_clients):
        remote_cmd(f"sudo docker exec -d client_{id_client} apt update", HOSTS['client']['ip'])

def install_packages_in_docker_conts(qty_clients, packages, id_start=1, id_stop=2):
    """
        Установка пакетов
    """
    for id_client in range(id_start, id_start+qty_clients):
        remote_cmd(f"sudo docker exec -d client_{id_client} apt install {' '.join(packages)} -y", HOSTS['client']['ip'])

def init_ipa_client(qty_clients, id_start=1, id_stop=2):
    """
        Ввод клиентов в домен
    """
    print(id_start+qty_clients)
    for id_client in range(id_start, id_start+qty_clients):
        # remote_exec(f"sudo docker exec -d client_{id_client} astra-freeipa-client -y -p {DC_PASSWORD}", 'client')
        # remote_cmd(f"sudo docker exec -d client_{id_client} astra-freeipa-client -y -p {DC_PASSWORD}", HOSTS['client']['ip'])
        remote_exec(f"sudo docker exec -d client_{id_client} ipa-client-install -w {DC_PASSWORD} -p admin --unattended", 'client')

def delete_ipa_client(qty_clients, id_start=1, id_stop=2):
    """
        Удаление клиентов из домена, остановка докер контейнеров и их удаление
    """
    for id_client in range(id_start, id_start+qty_clients):
        remote_cmd(f"ipa host-del client{id_client}", HOSTS['server']['ip'])

    # TODO Переделать в одну команду
    for id_client in range(id_start, id_start+qty_clients):
        remote_cmd(f"sudo docker stop client_{id_client}", HOSTS['client']['ip'])
    
    # TODO Переделать в одну команду
    for id_client in range(id_start, id_start+qty_clients):
        remote_cmd(f"sudo docker rm client_{id_client}", HOSTS['client']['ip'])


### TODO Если необходимо вывести клиентский компьютер из домена, вводим команду:
# ipa-client-install --uninstall

# На КД ipa host-del client{id}

#### Список всех хостов ipa host-find

##### sudo docker run -tid --name testing -h test.stress-testing.local --dns 10.177.5.171 --dns-search stress-testing.local -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run local/c7-systemd-freeipa-client
