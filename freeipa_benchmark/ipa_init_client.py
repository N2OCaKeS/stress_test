#### Ввод клиента в домен

from libs.libipa import cmd, remote_cmd, remote_exec, get_cmd_start, get_cmd_out
from ipa_conf import DC_PASSWORD, DOMAIN, USER, PASSWORD, HOSTS, ASTRA_VERSION_CLIENTS, QTY_IPA_CLIENTS, PASSWORD_DOCKER_CONT


def create_centos_cont(qty_clients=QTY_IPA_CLIENTS, id_start=1, id_stop=2):
    """
        Запуск контейнеров в фоне
    """
    for id_client in range(id_start, id_start+qty_clients):
        # remote_cmd(f"sudo docker run -d --name client_{id_client} -h client{id_client}.{DOMAIN} -v /parsecfs:/parsecfs -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run --privileged=true --dns {HOSTS['server']['ip']} --dns-search {DOMAIN} alse:{ASTRA_VERSION_CLIENTS} /usr/sbin/init", HOSTS['client']['ip'])
        # remote_exec(f"sudo docker run -d --name client_{id_client} -h client{id_client} --privileged=true centos /usr/sbin/init", 'client')
        remote_cmd(f"sudo docker run -tid --name client_{id_client} -h client{id_client}.{DOMAIN} --dns {HOSTS['server']['ip']} --dns-search {DOMAIN} -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run -p {10000 + id_client}:22 local/c7-systemd-freeipa-client-testing-user", HOSTS['client']['ip'])
        # sleep(10)

def init_ipa_client(qty_clients=QTY_IPA_CLIENTS, id_start=1, id_stop=2):
    """
        Ввод клиентов в домен
    """
    # print(id_start+qty_clients)
    # for id_client in range(id_start, id_start+qty_clients):
        # remote_exec(f"sudo docker exec -d client_{id_client} astra-freeipa-client -y -p {DC_PASSWORD}", 'client')
        # remote_cmd(f"sudo docker exec -d client_{id_client} astra-freeipa-client -y -p {DC_PASSWORD}", HOSTS['client']['ip'])
        ### ------------
        # remote_exec(f"sudo docker exec -d client_{id_client} ipa-client-install -w {DC_PASSWORD} -p admin --unattended", 'client')
        ### ------------
        # temp = remote_cmd(f"sudo docker exec -d client_{id_client} ipa-client-install -w {DC_PASSWORD} -p admin --unattended", HOSTS['client']['ip'])
        
        # temp = remote_cmd(f"sudo ipa-client-install -w {DC_PASSWORD} -p admin --unattended", HOSTS['client']['ip'], port=10000+id_client, background=False, passwd='docker')
        # print(temp)
    
    connections = []

    command = f"sudo ipa-client-install -w {DC_PASSWORD} -p admin --unattended"

    for id_client in range(id_start, id_start+qty_clients):
        ssh, pipes = get_cmd_start(cmd=command, host=HOSTS['client']['ip'], ssh_username=USER, ssh_pass=PASSWORD_DOCKER_CONT, port=10000+id_client, id_client=id_client)
        connections.append([ssh, *pipes])
    
    for ind, con in enumerate(connections):
        out = get_cmd_out(*con, ind)
        # print(out)


def delete_ipa_client(qty_clients=QTY_IPA_CLIENTS, id_start=1, id_stop=2):
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
