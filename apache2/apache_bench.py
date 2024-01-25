from libs.libapa import (command,
                         check_output_command)

requests = 10000
concurrency = 10
ip = check_output_command("hostname -I | awk '{print $1}'"); print(ip)
modules = ['proxy', 'proxy_http', 'proxy_balancer', 'lbmethod_byrequests', 'headers']
proxy_sett = r"""<VirtualHost *:80>
ServerName loadbalancer
RequestHeader set X-Remote-User expr=%{REMOTE_USER}
ProxyPreserveHost On
ProxyRequests Off
ServerName localhost
ProxyPass / "balancer://mycluster/"
ProxyPassReverse / "balancer://mycluster/"
AstraMode off
ProxyReceiveBufferSize 2048
ProxyTimeout 1
<Proxy balancer://mycluster>
        BalancerMember "http://localhost:8096"
        BalancerMember "http://localhost:8097"
</Proxy>
</VirtualHost>"""

proxy_member_sett_1 = r"""<VirtualHost *:8096>
    DocumentRoot /var/www/html
    ErrorLog ${APACHE_LOG_DIR}/error.log
    CustomLog ${APACHE_LOG_DIR}/access.log combined
</VirtualHost>"""

proxy_member_sett_2 = r"""<VirtualHost *:8097>
    DocumentRoot /var/www/html
    ErrorLog ${APACHE_LOG_DIR}/error.log
    CustomLog ${APACHE_LOG_DIR}/access.log combined
</VirtualHost>"""

print(f'ab -n {requests} -c {concurrency} http://{ip}:80/ > result.txt')

command('sudo apt-get install apache2 -y')
command('sudo sed -i -e \'s/# AstraMode on/AstraMode off/\' /etc/apache2/apache2.conf')
command('sudo sed -i -e \'s/AstraMode on/AstraMode off/\' /etc/apache2/apache2.conf')

with open('/etc/apache2/sites-available/000-default.conf', 'w')  as site:
    site.write(proxy_sett)

with open('/etc/apache2/ports.conf', 'a') as ports:
    ports.write('Listen 8096\nListen 8097\n')

with open('/etc/apache2/sites-available/001-member-8096.conf', 'w') as member_1:
    member_1.write(proxy_member_sett_1) 

with open('/etc/apache2/sites-available/002-member-8097.conf', 'w') as member_2:
    member_2.write(proxy_member_sett_2)     

[command(f'sudo a2enmod {module}') for module in modules]
command('sudo a2ensite 000-default.conf')
command('sudo a2ensite 001-member-8096.conf')
command('sudo a2ensite 002-member-8097.conf')
command('sudo systemctl restart apache2')

command(f'ab -n {requests} -c {concurrency} http://{ip}:80/ > result.txt')

with open('result.txt', 'r') as f:
    for line in f:
        if 'Requests per second:' in line:
            value = line.split(':')[1].strip()
            print(value.split(' ')[0])







# ip = check_output_command("hostname -I | awk '{print $1}'")
# modules = ['proxy', 'proxy_http', 'proxy_balancer', 'lbmethod_byrequests', 'headers']
# proxy_sett = r"""<VirtualHost *:80>
# ServerName loadbalancer
# RequestHeader set X-Remote-User expr=%{REMOTE_USER}
# ProxyPreserveHost On
# ProxyRequests Off
# ServerName localhost
# ProxyPass / "balancer://mycluster/"
# ProxyPassReverse / "balancer://mycluster/"
# AstraMode off
# ProxyReceiveBufferSize 2048
# ProxyTimeout 1
# <Proxy "balancer://mycluster/">
#         BalancerMember "http://localhost:8096"
# </Proxy>
# </VirtualHost>"""

# command('sudo apt-get install apache2 -y')

# with open('/etc/apache2/sites-available/000-default.conf', 'w')  as site:
#     site.write(proxy_sett)

# [command(f'sudo a2enmod {module}') for module in modules]
# command('sudo a2ensite 000-default.conf')
# command('sudo systemctl restart apache2')

# command(f'ab -n {requests} -c {concurrency} http://{ip}:80/ > result.txt')

# with open('result.txt', 'r') as f:
#     for line in f:
#         if 'Requests per second:' in line:
#             value = line.split(':')[1].strip()  
#             print(value.split(' ')[0])
#         else:
#             value = 'Nan'
#             print('Error: => Line "Requests per second" not found')

    

