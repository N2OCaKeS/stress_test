USER = "u"
PASSWORD = '1'

DOGTAG = True
DOMAIN = "stress-testing.local"
DC_PASSWORD = '12345678'

HOSTS = {
    'server': {
                'ip': '10.177.5.243'
              },
    'replica': {
                'ip': '10.177.5.22'
              },
    'client': {
                'ip': '10.177.5.84'
              }
}

ASTRA_VERSION_CLIENTS = "1.7.3.10"