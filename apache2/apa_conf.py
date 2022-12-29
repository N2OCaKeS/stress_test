from fabric import Connection


# ip address of tested server with apache2
TESTED_SERVER_IP = "10.0.20.23"
CLIENT_IP = "10.0.20.20"


# ssh ports  
TESTED_SERVER_SSH_PORT = 22
CLIENT_SSH_PORT = 22

# admin login creds for tested server
TESTED_SERVER_ADMIN_USER = "u"
TESTED_SERVER_ADMIN_PASS = "1"

CLIENT_ADMIN_USER = "u"
CLIENT_ADMIN_PASS = "1"

TESTED_QA_USER = "qa0"
TESTED_QA_USER_MAC = "qa1"
TESTED_QA_USER_MAC_CAT = "qa2"
TESTED_SERVER_QA_PASS = "1"

# tested server creds for connection instance
TESTED_SERVER_ADMIN_CREDS = {"password": TESTED_SERVER_ADMIN_PASS}
TESTED_SERVER_QA_CREDS = {"password": TESTED_SERVER_ADMIN_PASS}

CLIENT_ADMIN_CREDS = {"password": CLIENT_ADMIN_PASS}


# settings for apache benchmark 
MAX_CONCURRENCY = 200
CONCURRENCY_STEP = 25

MAX_REQUESTS = 2500


class ApacheNode:
    admin = Connection(
            host=TESTED_SERVER_IP, 
            user=TESTED_SERVER_ADMIN_USER, 
            connect_kwargs=TESTED_SERVER_ADMIN_CREDS, 
            port=TESTED_SERVER_SSH_PORT)
    
    qa0_login = Connection(host=TESTED_SERVER_IP,
                           user=TESTED_QA_USER,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=TESTED_SERVER_SSH_PORT)


    qa1_login = Connection(host=TESTED_SERVER_IP,
                           user=TESTED_QA_USER_MAC,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=TESTED_SERVER_SSH_PORT)

    qa2_login = Connection(host=TESTED_SERVER_IP,
                           user=TESTED_QA_USER_MAC_CAT,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=TESTED_SERVER_SSH_PORT)

class ClientNode:
    admin = Connection(
            host=CLIENT_IP, 
            user=CLIENT_ADMIN_USER, 
            connect_kwargs=CLIENT_ADMIN_CREDS, 
            port=CLIENT_SSH_PORT)
    
    qa0_login = Connection(host=CLIENT_IP,
                           user=TESTED_QA_USER,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=CLIENT_SSH_PORT)


    qa1_login = Connection(host=CLIENT_IP,
                           user=TESTED_QA_USER_MAC,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=CLIENT_SSH_PORT)

    qa2_login = Connection(host=CLIENT_IP,
                           user=TESTED_QA_USER_MAC_CAT,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=CLIENT_SSH_PORT)

