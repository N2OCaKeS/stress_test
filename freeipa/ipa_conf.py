SCRIPT_DIR = '/home/u/freeipa_test/gitipa/stress_test/freeipa'
LOG_FILENAME = f'{SCRIPT_DIR}/ipa_log'
REPORT_PATH = f'{SCRIPT_DIR}/report'
INFO_FILENAME = f"{SCRIPT_DIR}/ipa_info.json"
TEMPLATE_PATH = f'{SCRIPT_DIR}/templates'

USER = "u"
PASSWORD = '1'

REPLICA = False
DOMAIN = "stress-testing.local"
DC_PASSWORD = '12345678'

HOSTS = {
    'server': {
                'ip': '10.177.103.204'
              },
    'replica': {
                'ip': '',
              },
    'clients': {
                'ip': '10.177.103.201',
                },
    # 'hosts-with-clients': {
    #             'ip': ['10.177.5.184', '10.177.5.104']
    #           }
}

MAX_USERS_AUTH = 3000
USERS_AUTH_STEP = 500

'''
    Описание для графиков отчета
'''
GRAPH_DESCRIPTIONS = {
    'proc_errors.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости числа непройденных аутентиф. и авториз. в секунду  от количества клиентов, одновременно выполняющих транзакции.<ul><li><b>OX</b>: Количество клиентов, одновременно выполняющих аутентиф. и авториз.;</li><li><b>OY</b>: Процент ошибок</li><li><b>Функция</b>: Аппроксимирующая функция точек</li></ul></p>',
    'sr_znach.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости средней задержки аутентификации и авторизации сервиса от количества клиентов, одновременно выполняющих транзакции.<ul><li><b>OX</b>: Количество клиентов, одновременно выполняющих аутентиф. и авториз.;</li><li><b>OY</b>: Средняя задержка отклика сервиса;</li><li><b>Функция</b>: Аппроксимирующая функция точек;</li></ul></p>',
    'values_last.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости почти последней задержки аутентификации и авторизации сервиса от количества клиентов, одновременно выполняющих транзакции.<ul><li><b>OX</b>: Количество клиентов, одновременно выполняющих аутентиф. и авториз.;</li><li><b>OY</b>: Задержка отклика сервиса;</li><li><b>Функция</b>: Аппроксимирующая функция точек;</li></ul></p>',
}

"""
    Нижеописанное больше не используется, предназначалось для enroll теста
"""
# ASTRA_VERSION_CLIENTS = "1.7.3.10"
# LOWER_LIMITE_CLIENTS = 1
# STEP_CLIENTS = 1
# UPPER_LIMITE_CLIENTS = 15
# QTY_IPA_CLIENTS = 2
# EXT_REPO = 'deb ftp://10.177.5.111/astra/stable/1.7/extended-repository-3.1 1.7_x86-64 main contrib non-free'
# EXT_REPO = 'deb ftp://qa111.devos.astralinux.ru/astra/stable/1.7/extended-repository-3.1 1.7_x86-64 main contrib non-free'
# PASSWORD_DOCKER_CONT = 'docker'
# DOCKER_IMAGE_FOR_IPA_CLIENT = 'vanyawrestling/presetting-for-freeipa-client:centos7'
# COMMAND_RUN_DOCKER_CONT = 'docker run -tid --name {name} -h {hostname} --privileged=true --dns {dns_server} --dns-search {dns_domain} -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run -p {out_port}:22 {docker_image_for_ipa_client}'
# COMMAND_IPA_CLIENT_INSTALL = f"sudo ipa-client-install -w {DC_PASSWORD} -p admin --unattended"
# USERS_COUNT = 100
# sudo docker run -tid --name client_{id_client} -h client{id_client}.{DOMAIN} --dns {HOSTS['server']['ip']} --dns-search {DOMAIN} -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run -p {10000 + id_client}:22 vanyawrestling/presetting-for-freeipa-client:centos7