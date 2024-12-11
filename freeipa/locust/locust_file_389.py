
import time
from locust import User, task, between, events
from bonsai import LDAPClient, ConnectionError, TimeoutError, LDAPError
from bonsai.gevent import GeventLDAPConnection


class LdapUser(User):
#    wait_time = between(1, 2.5)

    def on_start(self):
        self.bind_addr = '10.177.103.202'
        self.bind_dn = "cn=Directory Manager"  
        self.bind_password = "password"  
        self.request_timeout = 5
        self.ldap_smpl_auth = None

    def ldap_bind(self):
        start_perf_counter = time.perf_counter()
        exception = None
        try:
            client = LDAPClient(f"ldap://{self.bind_addr}")
            client.set_credentials("SIMPLE", user=self.bind_dn, password=self.bind_password)
            client.set_async_connection_class(GeventLDAPConnection)
            self.ldap_smpl_auth = client.connect(is_async=True, timeout=self.request_timeout)
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="LDAP",
                name="simple bind",
                code=0,
                exception=None,
                context=None,
                response_time=response_time,
                response_length=0,)
        except (ConnectionError, TimeoutError, Exception) as exception:
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="LDAP",
                name="simple bind",
                code=1,
                exception=exception,
                context=None,
                response_time=response_time,
                response_length=len(str(exception)),)

    def ldap_unbind(self):
        start_perf_counter = time.perf_counter()
        try:
            self.ldap_smpl_auth.close()
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="LDAP",
                name="unbind",
                code=0,
                exception=None,
                context=None,
                response_time=response_time,
                response_length=0,)
        except (LDAPError, Exception) as exception:
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="LDAP",
                name="unbind",
                code=1,
                exception=exception,
                context=None,
                response_time=response_time,
                response_length=len(str(exception)),)

    @task
    def perform_bind(self):
        self.ldap_bind()
        self.ldap_unbind()


#sudo locust -f locustfile389.py --spawn-rate 5 --process 10 --headless -u 10 --run-time 30s

