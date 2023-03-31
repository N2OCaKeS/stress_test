USER = "u"
PASSWORD = '1'

DOGTAG = True
DOMAIN = "stress-testing.local"
DC_PASSWORD = '12345678'

PASSWORD_DOCKER_CONT = 'docker'

HOSTS = {
    'server': {
                'ip': '10.177.5.171'
              },
    'replica': {
                'ip': '10.177.5.95'
              },
    'client': {
                'ip': '10.177.124.107' # brest 10.177.124.107
              }
}

ASTRA_VERSION_CLIENTS = "1.7.3.10"

QTY_IPA_CLIENTS = 250

EXT_REPO = 'deb ftp://10.177.5.111/astra/stable/1.7/extended-repository-3.1 1.7_x86-64 main contrib non-free'