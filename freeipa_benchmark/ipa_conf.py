from math import ceil

SCRIPT_DIR = '/home/ivelikanov/git/stress_test/freeipa_benchmark'
LOG_FILENAME = '{}/ipa_log'.format(SCRIPT_DIR)
REPORT_PATH = '{}/report'.format(SCRIPT_DIR)

USER = "u"
PASSWORD = '1'

DOGTAG = True
DOMAIN = "stress-testing.local"
DC_PASSWORD = '12345678'

PASSWORD_DOCKER_CONT = 'docker'

HOSTS = {
    'server': {
                'ip': '10.177.102.203'
              },
    'replica': {
                'ip': '10.177.102.202'
              },
    'hosts-with-clients': {
                'ip': ['10.177.5.184', '10.177.5.104']
              }
}

ASTRA_VERSION_CLIENTS = "1.7.3.10"


LOWER_LIMITE_CLIENTS = 1
STEP_CLIENTS = 1
UPPER_LIMITE_CLIENTS = 15

# QTY_IPA_CLIENTS = 2

# EXT_REPO = 'deb ftp://10.177.5.111/astra/stable/1.7/extended-repository-3.1 1.7_x86-64 main contrib non-free'
EXT_REPO = 'deb ftp://qa111.devos.astralinux.ru/astra/stable/1.7/extended-repository-3.1 1.7_x86-64 main contrib non-free'

DOCKER_IMAGE_FOR_IPA_CLIENT = 'vanyawrestling/presetting-for-freeipa-client:centos7'

COMMAND_RUN_DOCKER_CONT = 'docker run -tid --name {name} -h {hostname} --privileged=true --dns {dns_server} --dns-search {dns_domain} -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run -p {out_port}:22 {docker_image_for_ipa_client}'

COMMAND_IPA_CLIENT_INSTALL = f"sudo ipa-client-install -w {DC_PASSWORD} -p admin --unattended"

USERS_COUNT = 100

# sudo docker run -tid --name client_{id_client} -h client{id_client}.{DOMAIN} --dns {HOSTS['server']['ip']} --dns-search {DOMAIN} -v /sys/fs/cgroup:/sys/fs/cgroup:ro -v /tmp/$(mktemp -d):/run -p {10000 + id_client}:22 vanyawrestling/presetting-for-freeipa-client:centos7