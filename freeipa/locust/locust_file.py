
import bonsai
#import gssapi
import time
import uuid
import socket
import re
import logging
import subprocess
from locust import User, events, task, FastHttpUser
from locust.exception import InterruptTaskSet, StopUser
from locust_plugins.listeners.timescale import Timescale
from bonsai.gevent import GeventLDAPConnection


@events.init_command_line_parser.add_listener
def add_arguments(parser):
    parser.add_argument('--use_bind', action='store_true', default=True, help='Подключение к LDAP через SIMPLE bind')
    parser.add_argument('--use_kinit', action='store_true', default=True, help='Получение билета Kerberos')
    parser.add_argument('--use_dns', action='store_true', default=True, help='Отправить DNS запрос')
    parser.add_argument('--use_search', action='store_true', default=True, help='Поиск записей через LDAP search')
    parser.add_argument('--use_add', action='store_true', default=False, help='Добавление пользователей LDAP')
    parser.add_argument('--use_unbind', action='store_true', default=True, help='Закрытие LDAP соединения')
    parser.add_argument('--ldap-bind-dn', type=str, default='uid=admin,cn=users,cn=accounts,dc=ald,dc=pro')
    parser.add_argument('--ldap-bind-rbta', type=str, default='ou=ald.pro,cn=orgunits,cn=accounts,dc=ald,dc=pro')
    parser.add_argument('--ldap-bind-addr', type=str, default='10.177.103.204')
    parser.add_argument('--ldap-bind-password', type=str, is_secret=True, default='12345678')
    parser.add_argument('--ldap-search-base', type=str, default='cn=users,cn=accounts,dc=ald,dc=pro')
    parser.add_argument('--ldap-search-filter', type=str, default='(uid=*)')
    parser.add_argument('--ldap-search-attrlist', type=str, default='uid,rbtadp')
    parser.add_argument('--kerberos-realm', type=str, default='ALD.PRO')
    parser.add_argument('--kerberos-password', type=str, is_secret=True, default='12345678')
    parser.add_argument('--kerberos-user', type=str, default='admin')
    parser.add_argument('--dns-host', type=str, default='ya.ru')
    parser.add_argument('--request-delay', type=int, default=0)
    parser.add_argument('--request-timeout', type=int, default=5)


class LDAPUser(User):
    abstract = True
    def __init__(self, environment):
        super().__init__(environment)
        self.client = LDAPClient(environment=environment)
        self.request_body = {
            'bind': False,
            'dns': False,
            'search': False,
            'add': False,
            'kinit': False,
            'unbind': False,
            # 'use_http': False,
        }
        if self.environment.parsed_options.use_bind:
            self.request_body['bind'] = True

        if self.environment.parsed_options.use_kinit:
            self.request_body['kinit'] = True

        if self.environment.parsed_options.use_dns:
            self.request_body['dns'] = True

        if self.environment.parsed_options.use_search:
            self.request_body['search'] = True

        if self.environment.parsed_options.use_add:
            self.request_body['add'] = True

        if self.environment.parsed_options.use_unbind:
            self.request_body['unbind'] = True


# Locustfile Client side              #
# It can be used as a client part     #
class LDAPconstructor(LDAPUser):
    def wait_time(self):
        return self.environment.parsed_options.request_delay

    @task
    def make_request(self):
        if ((not self.request_body['bind']) and (self.request_body['search'] or self.request_body['add'] or self.request_body['unbind'])):
            logging.info('It is required to set LDAPbind or LDAPkinit for SEARCH/ADD/UNBIND operations')
            if self.environment.runner.user_count == 1:
                logging.info("Last user stopped, quitting runner")
                self.environment.runner.quit()
            self.environment.runner.quit()
            raise StopUser()
            # raise InterruptTaskSet('It is required to set LDAPbind or LDAPkinit for SEARCH/ADD/UNBIND operations')

        self.all_start_perf_counter = time.perf_counter()

        if self.request_body['bind']:
            self.client.ldap_bind()

        if self.request_body['kinit']:
            self.client.kerberos_kinit()

        if self.request_body['dns']:
            self.client.resolve_dns()

        if self.request_body['search']:
            self.client.ldap_search()

        if self.request_body['add']:
            self.client.ldap_add()

        if self.request_body['unbind']:
            self.client.ldap_unbind()

        all_response_time = (time.perf_counter() - self.all_start_perf_counter) * 1000
        if self.environment.parsed_options.request_timeout * 1000 < all_response_time:
            self.environment.events.request.fire(
                request_type="ALL",
                name="combined",
                code=1,
                exception="Response time exceeded",
                context=None,
                response_time=all_response_time,
                response_length=0,
            )
        else:
            self.environment.events.request.fire(
                request_type="ALL",
                name="combined",
                code=0,
                exception=None,
                context=None,
                response_time=all_response_time,
                response_length=0,
            )


#######################################


class LDAPClient:
    def __init__(self, environment):
        self.environment = environment
        self.bind_dn = self.environment.parsed_options.ldap_bind_dn
        self.bind_password = self.environment.parsed_options.ldap_bind_password
        self.bind_addr = self.environment.parsed_options.ldap_bind_addr
        self.bind_rbta = self.environment.parsed_options.ldap_bind_rbta
        self.kerberos_realm = self.environment.parsed_options.kerberos_realm
        self.kerberos_user = self.environment.parsed_options.kerberos_user
        self.kerberos_password = self.environment.parsed_options.kerberos_password
        self.request_timeout = self.environment.parsed_options.request_timeout
        self.dns_host = self.environment.parsed_options.dns_host
        self.ldap_search_base = self.environment.parsed_options.ldap_search_base
        self.ldap_search_filter = self.environment.parsed_options.ldap_search_filter
        self.ldap_search_attrlist = re.split((r'[,:+ ]+'),self.environment.parsed_options.ldap_search_attrlist)

    def generate_ldap_schemes(self):
        self.rand_short_uuid = str(uuid.uuid4())[:16]
        self.entry_dn = 'uid=locustest_' + self.rand_short_uuid + ',' + ','.join(self.bind_dn.split(",")[1:])
        self.bind_entry = {
            'objectClass': [
                'ipaobject',
                'person',
                'top',
                'ipasshuser',
                'inetorgperson',
                'organizationalperson',
                'krbticketpolicyaux',
                'krbprincipalaux',
                'inetuser',
                'posixaccount',
                'ipaSshGroupOfPubKeys',
                'mepOriginEntry',
                'rbta-unit',
                'rbta-address',
                'rbta-inetorgperson-ext',
                'rbtaCustomUserAttrs',
                'rbtaUserMeta',
                'ruPostMailAccount',
                'x-ald-audit-policy',
                'x-ald-user-parsec14',
                'x-ald-user'
            ],
            'displayName': 'Базара Джексон Александрович',
            'gecos': 'Базара Джексон Александрович',
            'givenName': 'Джексон',
            'sn': 'Базара',
            'initials': 'БазараДА',
            'rbtamiddlename': 'Базара',
            'street': 'Пушкина 12',
            'l': 'Москва',
            'st': 'Москва',
            'postalCode': 123123,
            'c': 'RU',
            'employeeNumber': 1880,
            'telephonenumber': '+7 (800) 555-35-35',
            'title': 'Crook',
            'cn': 'locustest_' + self.rand_short_uuid + ' ' + self.rand_short_uuid,
            'uid': 'locustest_' + self.rand_short_uuid,
            'homeDirectory': '/home/locustest_' + self.rand_short_uuid,
            'krbPrincipalName': 'locustest_' + self.rand_short_uuid + '@' + self.kerberos_realm,
            'krbCanonicalName': 'locustest_' + self.rand_short_uuid + '@' + self.kerberos_realm,
            'rbtadp': self.bind_rbta,
            'ipaUniqueID': 'autogenerate',
            'loginShell': '/bin/sh',
            'uidNumber': '-1',
            'gidNumber': '-1',
            'krbExtraData': 'this-will-not-work-this-is-a-placeholder-for-IPA-framework-',
            # 'krbLastPwdChange': '20230408125354Z',
            # 'krbPasswordExpiration': '20240408125354Z',
            'userPassword' : 'password',
            'x-ald-user-mac' : '0:0x0:0:0x0'
        }

    def ldap_bind(self):
        start_perf_counter = time.perf_counter()
        try:
            self.client = bonsai.LDAPClient(f"ldap://{self.bind_addr}")
            self.client.set_credentials("SIMPLE", user=self.bind_dn, password=self.bind_password)
            self.client.set_async_connection_class(GeventLDAPConnection)
            self.ldap_smpl_auth = self.client.connect(is_async=True, timeout=self.request_timeout)
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="LDAP",
                name="simple bind",
                code=0,
                exception=None,
                context=None,
                response_time=response_time,
                response_length=0,
            )
        except (bonsai.ConnectionError, bonsai.TimeoutError, Exception) as exception:
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="LDAP",
                name="simple bind",
                code=1,
                exception=exception,
                context=None,
                response_time=response_time,
                response_length=len(str(exception)),
            )
            # raise RescheduleTask

    def kerberos_kinit(self):
        start_perf_counter = time.perf_counter()
        krb_ticket = ""
        try:
            subprocess.run(['kinit', f"{self.kerberos_user}@{self.kerberos_realm}"],
                            input=self.kerberos_password,
                            capture_output=True,
                            text=True).stdout
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="Kerberos",
                name="kinit",
                code=0,
                exception=None,
                context=None,
                response_time=response_time,
                response_length=len(str(krb_ticket)),
            )
        except (subprocess.CalledProcessError, Exception) as exception:
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="Kerberos",
                name="kinit",
                code=1,
                exception=exception,
                context=None,
                response_time=response_time,
                response_length=len(str(exception)),
            )


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
                response_length=0,
            )
        except (bonsai.LDAPError, Exception) as exception:
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="LDAP",
                name="unbind",
                code=1,
                exception=exception,
                context=None,
                response_time=response_time,
                response_length=len(str(exception)),
            )

    def ldap_add(self):
        self.generate_ldap_schemes()
        start_perf_counter = time.perf_counter()
        ldap_add_res = ""
        try:
            entry = bonsai.LDAPEntry(self.entry_dn)
            entry.update(self.bind_entry)
            ldap_add_res = self.ldap_smpl_auth.add(entry)
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="LDAP",
                name="add",
                code=0,
                exception=None,
                context=None,
                response_time=response_time,
                response_length=len(str(ldap_add_res)),
            )
        except (bonsai.LDAPError, bonsai.ConnectionError, bonsai.TimeoutError,
                bonsai.AlreadyExists, bonsai.ObjectClassViolation, Exception) as exception:
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="LDAP",
                name="add",
                code=1,
                exception=exception,
                context=None,
                response_time=response_time,
                response_length=len(str(exception)),
            )

    def ldap_search(self):
        start_perf_counter = time.perf_counter()
        ldap_search_res = ""
        try:
            ldap_search_res = self.ldap_smpl_auth.paged_search(self.ldap_search_base, 2, filter_exp=self.ldap_search_filter, attrlist=self.ldap_search_attrlist)
            # Without paged_search leads to LDAP search: SizeLimitError('Size limit exceeded.')
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="LDAP",
                name="search",
                code=0,
                exception=None,
                context=None,
                response_time=response_time,
                response_length=len(str(ldap_search_res)),
            )
        except (bonsai.LDAPError, bonsai.ConnectionError, bonsai.TimeoutError, Exception) as exception:
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="LDAP",
                name="search",
                code=1,
                exception=exception,
                context=None,
                response_time=response_time,
                response_length=len(str(exception)),
            )

    def resolve_dns(self):
        start_perf_counter = time.perf_counter()
        dns_response = ""
        try:
            # socket.getaddrinfo(self.dns_host, self.dns_port)
            dns_response = socket.gethostbyname(self.dns_host)
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="DNS",
                name="get_addr",
                code=0,
                exception=None,
                context=None,
                response_time=response_time,
                response_length=len(str(dns_response)),
            )
        except (socket.gaierror, Exception) as exception:
            response_time = (time.perf_counter() - start_perf_counter) * 1000
            self.environment.events.request.fire(
                request_type="DNS",
                name="get_addr",
                code=1,
                exception=exception,
                context=None,
                response_time=response_time,
                response_length=len(str(exception)),
            )
