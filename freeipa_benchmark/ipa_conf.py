USER = "u"
PASSWORD = '1'

DOGTAG = True
DOMAIN = "stress-testing.local"
DC_PASSWORD = '12345678'

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