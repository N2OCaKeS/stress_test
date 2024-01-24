#### Ввод клиента в домен

from math import ceil

from libs.libipa import cmd, remote_cmd, remote_exec, get_cmd_start, get_cmd_out
from ipa_conf import DC_PASSWORD, DOMAIN, USER, PASSWORD, HOSTS, PASSWORD_DOCKER_CONT, DOCKER_IMAGE_FOR_IPA_CLIENT, COMMAND_RUN_DOCKER_CONT, COMMAND_IPA_CLIENT_INSTALL, UPPER_LIMITE_CLIENTS

def presettings_on_hosts_for_ipa_clients():
    for ip_client_host in HOSTS['hosts-with-clients']['ip']:
        print(ip_client_host)
        out_install = remote_cmd(command="sudo apt install docker.io -y", host=ip_client_host)
        print(out_install)
        out_docker_pull = remote_cmd(command=f"sudo docker pull {DOCKER_IMAGE_FOR_IPA_CLIENT}", host=ip_client_host)
        print(out_docker_pull)


def check_qty_clients(due_amount):
    remote_cmd(command=f"echo {DC_PASSWORD} | kinit admin", host=HOSTS['server']['ip'])
    out_host_find = remote_cmd(command="ipa host-find --sizelimit=0", host=HOSTS['server']['ip'])
    print(out_host_find)
    qty_all_clients = remote_cmd(command="ipa host-find --sizelimit=0 | grep 'Количество'", host=HOSTS['server']['ip']).split(" ")[3]
    # print(qty_all_clients)
    # TODO не плюс один а плюс 2 на постоянке
    losses = due_amount + 1 - int(qty_all_clients)
    # losses = int(qty_all_clients) - due_amount - 1
    return losses 


def create_centos_cont(qty_clients):
    """
        Запуск контейнеров в фоне
    """

    hosts_with_clients = HOSTS['hosts-with-clients']['ip'].copy()
    number_ipa_client = 1
    for ip_client_host in hosts_with_clients:
        if qty_clients < len(hosts_with_clients):
            hosts_with_clients.pop(-1)
        for _ in range(ceil(qty_clients / len(hosts_with_clients))):
            out = remote_cmd(command=COMMAND_RUN_DOCKER_CONT.format(
                                                            name=f"client_{number_ipa_client}",
                                                            hostname=f"client{number_ipa_client}.{DOMAIN}",
                                                            dns_server=HOSTS['server']['ip'],
                                                            dns_domain=DOMAIN,
                                                            out_port=10000+number_ipa_client,
                                                            docker_image_for_ipa_client=DOCKER_IMAGE_FOR_IPA_CLIENT
                                                            ), host=ip_client_host)
            
            print(number_ipa_client, out)
            
            number_ipa_client += 1
            if number_ipa_client > qty_clients:
                break


    # for id_client in range(id_start, id_start+qty_clients):
        # remote_cmd(f"sudo docker run -d --name client_{id_client} -h client{id_client}.{DOMAIN} -v /parsecfs:/parsecfs -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run --privileged=true --dns {HOSTS['server']['ip']} --dns-search {DOMAIN} alse:{ASTRA_VERSION_CLIENTS} /usr/sbin/init", HOSTS['client']['ip'])
        # remote_exec(f"sudo docker run -d --name client_{id_client} -h client{id_client} --privileged=true centos /usr/sbin/init", 'client')
        # remote_cmd(f"sudo docker run -tid --name client_{id_client} -h client{id_client}.{DOMAIN} --dns {HOSTS['server']['ip']} --dns-search {DOMAIN} -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run -p {10000 + id_client}:22 local/c7-systemd-freeipa-client-testing-user", HOSTS['client']['ip'])
        # sleep(10)
        # remote_cmd(f"sudo docker run -tid --name client_{id_client} -h client{id_client}.{DOMAIN} --dns {HOSTS['server']['ip']} --dns-search {DOMAIN} -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run -p {10000 + id_client}:22 vanyawrestling/presetting-for-freeipa-client:centos7", HOSTS['client']['ip'])
        

def init_ipa_client(qty_clients):
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
    
    hosts_with_clients = HOSTS['hosts-with-clients']['ip'].copy()
    number_ipa_client = 1
    for ip_client_host in hosts_with_clients:
        if qty_clients < len(hosts_with_clients):
            hosts_with_clients.pop(-1)
        for _ in range(ceil(qty_clients / len(hosts_with_clients))):
            ssh, pipes = get_cmd_start(cmd=COMMAND_IPA_CLIENT_INSTALL, host=ip_client_host, ssh_username=USER, ssh_pass=PASSWORD_DOCKER_CONT, port=10000+number_ipa_client)
            connections.append([ssh, *pipes])
            print(f"cmd_start: {number_ipa_client}")
            number_ipa_client += 1
            if number_ipa_client > qty_clients:
                break
    

    # for id_client in range(id_start, id_start+qty_clients):
    #     ssh, pipes = get_cmd_start(cmd=COMMAND_IPA_CLIENT_INSTALL, host=HOSTS['client']['ip'], ssh_username=USER, ssh_pass=PASSWORD_DOCKER_CONT, port=10000+id_client)
    #     connections.append([ssh, *pipes])
    
    for ind, con in enumerate(connections):
        out = get_cmd_out(*con)
        # print(f"cmd_out: {ind + 1}")
        # file = open(f"clients/{ind + 1}.txt", "w")
        # file.write(out)
        # file.close()
        # print(out)


def delete_clients_from_dc(qty_clients):
    """
        Удаление клиентов из домена
    """
    remote_cmd(command=f"echo -e {DC_PASSWORD} | kinit admin", host=HOSTS['server']['ip'])
    for id_client in range(1, qty_clients+1):
        out_delete = remote_cmd(f"ipa host-del client{id_client}", HOSTS['server']['ip'])
        print(out_delete)

    # for id_client in range(id_start, id_start+qty_clients):
    #     remote_cmd(f"ipa host-del client{id_client}", HOSTS['server']['ip'])


def delete_clients_from_hosts(qty_clients):
    # ipa-client-install --uninstall
    connections = []
    
    hosts_with_clients = HOSTS['hosts-with-clients']['ip'].copy()
    number_ipa_client = 1
    for ip_client_host in hosts_with_clients:
        if qty_clients < len(hosts_with_clients):
            hosts_with_clients.pop(-1)
        for _ in range(ceil(qty_clients / len(hosts_with_clients))):
            ssh, pipes = get_cmd_start(cmd="echo -e 'no' | sudo ipa-client-install --uninstall", host=ip_client_host, ssh_username=USER, ssh_pass=PASSWORD_DOCKER_CONT, port=10000+number_ipa_client)
            connections.append([ssh, *pipes])
            
            number_ipa_client += 1
            if number_ipa_client > qty_clients:
                break
    
    for ind, con in enumerate(connections):
        out = get_cmd_out(*con)
        # print(out)



def delete_docker_cont(qty_clients):
    """
        Остановка докер контейнеров и их удаление
    """
    connections = []

    hosts_with_clients = HOSTS['hosts-with-clients']['ip'].copy()
    number_ipa_client = 1
    for ip_client_host in hosts_with_clients:
        if qty_clients < len(hosts_with_clients):
            hosts_with_clients.pop(-1)
        for _ in range(ceil(qty_clients / len(hosts_with_clients))):
            
            # out = remote_cmd(command=f"docker stop client_{number_ipa_client}", host=ip_client_host)
            # print(number_ipa_client, out, ip_client_host)
            
            ssh, pipes = get_cmd_start(cmd=f"docker stop client_{number_ipa_client}", host=ip_client_host, ssh_username=USER, ssh_pass=PASSWORD)
            connections.append([ssh, *pipes])

            number_ipa_client += 1
            if number_ipa_client > qty_clients:
                break
    
    for con in connections:
        get_cmd_out(*con)

    
    # # TODO Переделать в одну команду
    # for id_client in range(id_start, id_start+qty_clients):
    #     remote_cmd(f"sudo docker stop client_{id_client}", HOSTS['client']['ip'])

    number_ipa_client = 1
    for ip_client_host in HOSTS['hosts-with-clients']['ip']:
        for _ in range(ceil(qty_clients / len(HOSTS['hosts-with-clients']['ip']))):
            remote_cmd(command=f"docker rm client_{number_ipa_client}", host=ip_client_host)
            number_ipa_client += 1
            if number_ipa_client > qty_clients:
                break

    # # TODO Переделать в одну команду
    # for id_client in range(id_start, id_start+qty_clients):
    #     remote_cmd(f"sudo docker rm client_{id_client}", HOSTS['client']['ip'])


### TODO Если необходимо вывести клиентский компьютер из домена, вводим команду:
# ipa-client-install --uninstall

# На КД ipa host-del client{id}

#### Список всех хостов ipa host-find

##### sudo docker run -tid --name testing -h test.stress-testing.local --dns 10.177.5.171 --dns-search stress-testing.local -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run local/c7-systemd-freeipa-client
