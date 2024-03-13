from libs.libapa import (command,
                         check_output_command)
import os
import numpy as np
from apa_conf import REPORT_PATH, REPORT_FILENAME, REPETITIONS_COUNTER, REQUESTS, CONCURRENCY, \
                     VALID_VALUES_PERCENT, PERCENT_LIMIT



class ApacheBenchReverseProxy:
    def __init__(self):
        #Количество запусков бенчмарка
        self.repetitions_counter = REPETITIONS_COUNTER
        #Запросы
        self.requests = REQUESTS
        #Потоки
        self.concurrency = CONCURRENCY
        #Лимит группы по количеству элементов, принимаемой к расчетам, в %
        self.valid_values_percent = VALID_VALUES_PERCENT
        #Лимит отклонения, в %
        self.percent_limit = PERCENT_LIMIT

    def run(self):
        ip = check_output_command("hostname -I | awk '{print $1}'"); print(ip)
        modules = ['proxy', 'proxy_http', 'proxy_balancer', 'lbmethod_byrequests', 'headers', 'mpm_event']
        sites = ['000-default.conf', '001-member-8096.conf', '002-member-8097.conf']

        html_page = """<!DOCTYPE html>
        <html>
        <body>
        Hello
        </body>
        </html>"""

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


        def start_benchmark(count):
            print(f'ab -n {self.requests} -c {self.concurrency} http://{ip}:80/ >> result.txt')
            [command(f'ab -n {self.requests} -c {self.concurrency} http://{ip}:80/ >> result.txt') for i in range(count)]

        command('sudo apt-get install apache2 -y')
        command('sudo sed -i -e \'s/# AstraMode on/AstraMode off/\' /etc/apache2/apache2.conf')
        command('sudo sed -i -e \'s/AstraMode on/AstraMode off/\' /etc/apache2/apache2.conf')

        with open('/etc/apache2/sites-available/000-default.conf', 'w')  as site:
            site.write(proxy_sett)

        with open('/var/www/html/index.html', 'w') as index:
            index.write(html_page)

        with open('/etc/apache2/ports.conf', 'a') as ports:
            ports.write('Listen 8096\nListen 8097\n')

        with open('/etc/apache2/sites-available/001-member-8096.conf', 'w') as member_1:
            member_1.write(proxy_member_sett_1) 

        with open('/etc/apache2/sites-available/002-member-8097.conf', 'w') as member_2:
            member_2.write(proxy_member_sett_2)     

        command('sudo systemctl stop apache2')
        command('sudo a2dismod mpm_prefork')
        [command(f'sudo a2enmod {module}') for module in modules]
        [command(f'sudo a2ensite {site}') for site in sites]
        command('sudo systemctl restart apache2')

        if os.path.isfile('result.txt'):
            os.remove('result.txt')

        start_benchmark(self.repetitions_counter)

        with open('result.txt', 'r') as f:
            result_file = f.readlines()
        values = [v.split(':')[1].strip().split(' ')[0] for v in result_file if 'Requests per second:' in v]; print(values)
        ab_values = np.array([int(float(x)) for x in values if x.replace('.', '', 1).isdigit()]); print(ab_values)


        def check_value(value, all_values, percent_limit):
            diffs = np.abs((all_values - value) / value * 100)
            return np.sum(diffs <= percent_limit) >= len(all_values) / 2


        valid_values = [value for value in ab_values if check_value(value, ab_values, self.percent_limit)]
        novalid_values = [value for value in ab_values if value not in valid_values]
        print('Используемые в расчетах значения:', valid_values)
        print('Отсеянные значения:', novalid_values)

        if len(valid_values) >= len(ab_values) * self.valid_values_percent / 100:
            mean_cleaned = np.mean(valid_values)
            print(f"Среднее значение без учета аномалий: {int(mean_cleaned)}")
        else:
            mean_cleaned = 'NaN'
            print('Нет подходящих групп значений для расчета среднего')

        with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'w') as w:
            w.write(mean_cleaned)
    

