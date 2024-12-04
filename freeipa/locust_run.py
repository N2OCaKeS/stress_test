from libs.libipa import (remote_put_file,
                         remote_exec)



AV = '1.7.6.UU.2.1'
USER = 'u'
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



remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_conf.py', "ipa_conf.py")
remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_init_dc.py', "ipa_init_dc.py")
remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/prepare.sh', "prepare.sh")
remote_exec(f"sudo bash prepare.sh {AV}", 'server')
remote_exec("sudo python3 ipa_init_dc.py", 'server')


