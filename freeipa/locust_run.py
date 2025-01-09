from libs.libipa import remote_put_file
from time import sleep
from fabric import Connection


AV = '1.7.6.UU.2.1'
USER = 'u'
PASSWORD = '1'
HOSTS = {
    'server': {
                'ip': '10.177.103.204'
              },
    'replica': {
                'ip': '',
              },
    'clients': {
                'ip': '10.177.103.205',
                }
}



def host_is_available(node):
    try:
        with Connection(host=HOSTS[node]['ip'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as node_client:
            if str(node_client.run('uptime')):
                return True
    except Exception:
        return False

def remote_exec(command, node="server"):
    while host_is_available(node) == False:
        sleep(1)
        print("{host} временно не доступен...повторная попытка подключения...".format(host=HOSTS[node]['ip']))
    try:
        with Connection(host=HOSTS[node]['ip'],
                            user=USER,
                            connect_kwargs={"password": PASSWORD}) as node_client:
            return str(node_client.run(command))
    except Exception as err:
        return "Что то пошло не так...{message}".format(message=err)


remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_conf.py', "ipa_conf.py")
remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_init_dc.py', "ipa_init_dc.py")
remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/prepare.sh', "prepare.sh")
remote_exec(f"sudo bash prepare.sh {AV}", 'server')
remote_exec("sudo python3 ipa_init_dc.py", 'server')





#locust -f ../locust/locust_file.py --use_bind --use_unbind --headless -u 10 --run-time 30s

#for i in {1..25}; do nohup locust -f ../locust/locust_file.py --worker --master-host=localhost & done
#locust -f ../locust/locust_file.py --master --headless -u 10 --run-time 30s


#sudo locust -f ../locust/locust_file.py --process 10 --headless -u 20 --run-time 2m