import time
import ldap
from ldap.asyncsearch import List
from multiprocessing import Process, Barrier, Value, Manager, Array

def auth(user_id, array_for_ldap_error):
    try:
        l = ldap.initialize("ldap://stand-1-i711700-32-low.stress-testing.local")
        l.protocol_version = ldap.VERSION3
        username = f"uid=user{user_id},cn=users,cn=compat,dc=stress-testing,dc=local" # введите DN (Distinguished Name) пользователя
        password  = "password" # введите пароль пользователя
        l.simple_bind_s(username, password)
        # l.search_s()
    except ldap.INVALID_CREDENTIALS as e:
        # print("Your username or password is incorrect.")
        array_for_ldap_error[user_id] = e
        return False
    except ldap.LDAPError as e:
        array_for_ldap_error[user_id] = e
        return False
    # whoami = l.whoami_s()
    return l

def auth_out(ldap_obj):
    ldap_obj.unbind_s()

def async_ldap_search(ldap_obj, **kwargs):
    """
        Асинхронный search
    """
    if 'attrlist' in kwargs:
        attr_list = kwargs['attrlist']
    else:
        attr_list = None
    s = ldap.asyncsearch.List(ldap_obj)
    s.startSearch(
        kwargs['base'],
        kwargs['scope'],
        kwargs['search_filter'],
        attr_list
    )
    try:
        partial = s.processResults()
    except ldap.SIZELIMIT_EXCEEDED as err:
        return False, err
    except ldap.LDAPError as err:
        return False, err

    if len(s.allResults) > 0:
        allres = []
        for result in s.allResults:
            allres.append(result[1])
        return True, allres
    return True, s.allResults

def _ldap_search(ldap_obj, base, scope, search_filter, attrlist=None):
    # TODO прокинуть сюда array с ошибками
    try:
        res = ldap_obj.search_s(base, scope, search_filter, attrlist)
    except ldap.LDAPError as e:
        return False, e
    return True, res

def _ldap_search_old(ldap_obj, base, scope, search_filter, attrlist):
    try:
        ldap_result_id = ldap_obj.search(base, scope, search_filter, attrlist)
        result_set = []
        while True:
            result_type, result_data = ldap_obj.result(ldap_result_id, 0)
            if len(result_data) == 0:
                break
            else:
                if result_type == ldap.RES_SEARCH_ENTRY:
                    result_set.append(result_data)
    except ldap.LDAPError as e:
        return False, e
    return True, result_set[0]

def autorize(ldap_obj, user_id):
    SCOPE = ldap.SCOPE_SUBTREE
    domain_dn = "dc=stress-testing,dc=local"
    domain = "stress-testing.local"
    # TODO ВЫТЯГИВАТЬ из конфига
    host = "stand-1-i711700-32-low"
    
    res_code, user_entry = _ldap_search(ldap_obj=ldap_obj, 
                                        base=f'cn=accounts,{domain_dn}', 
                                        scope=SCOPE, 
                                        search_filter=f'(&(uid=user{user_id})(objectClass=posixAccount)(uid=*)(&(uidNumber=*)(!(uidNumber=0))))', 
                                        attrlist=["objectClass", "uid",
                                                  "userPassword", "uidNumber",
                                                  "gidNumber", "gecos",
                                                  "homeDirectory", "loginShell",
                                                  "krbPrincipalName", "cn",
                                                  "memberOf", "ipaUniqueID",
                                                  "ipaNTSecurityIdentifier", "modifyTimestamp",
                                                  "entryUSN", "x-ald-user-caps",
                                                  "x-ald-user-mac", "x-ald-user-mic-level",
                                                  "shadowLastChange", "shadowMin",
                                                  "shadowMax", "shadowWarning",
                                                  "shadowInactive", "shadowExpire",
                                                  "shadowFlag", "krbLastPwdChange",
                                                  "krbPasswordExpiration",
                                                  "authorizedService"]
                                                  )
    # if len(user_entry) == 0:
    #     print('User not found!!!')
    # print(user_entry[0][1]["ipaUniqueID"])
    uuid = user_entry[0][1]["ipaUniqueID"][0].decode("utf-8")
    gidNumber = user_entry[0][1]['gidNumber'][0].decode("utf-8")
    # print(uuid, type(uuid))
    # print(gidNumber)

    # TODO ПРОВЕРИТЬ!
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=Default Trust View,cn=views,cn=accounts,{domain_dn}",
                 scope=SCOPE,
                 search_filter=f"(&(objectClass=ipaOverrideAnchor)(ipaAnchorUUID=:IPA:stress-testing.local:{uuid}))")
    
    res_code, user_groups_entry = _ldap_search(ldap_obj=ldap_obj,
                                               base=f"cn=accounts,{domain_dn}",
                                               scope=SCOPE,
                                               search_filter=f"(&(uid=user{user_id})(objectclass=posixAccount)(&(uidNumber=*)(!(uidNumber=0))))",
                                               attrlist=["objectClass", "uid", "userPassword",  "uidNumber", "gidNumber", "gecos",  "homeDirectory", "loginShell", "krbPrincipalName",
                                                         "cn", "memberOf", "ipaUniqueID", "ipaNTSecurityIdentifier", "modifyTimestamp", "entryUSN", "x-ald-user-caps", "x-ald-user-mac", "x-ald-user-mic-level",
                                                         "shadowLastChange", "shadowMin", "shadowMax", "shadowWarning", "shadowInactive", "shadowExpire", "shadowFlag", "krbLastPwdChange", "krbPasswordExpiration",
                                                         "authorizedService", "nsAccountLock", "host", "ipaSshPubKey", "ipaUserAuthType", "userCertificate", "mail"])
    
    groups = user_groups_entry[0][1]["memberOf"]
    for group in groups:
        group = group.decode("utf-8")
        _ldap_search(ldap_obj=ldap_obj,
                     base=f"{group}",
                     scope=SCOPE,
                     search_filter=f"(&(|(objectClass=ipaUserGroup)(objectClass=posixGroup))(cn=*))",
                     attrlist=["objectClass", "cn",
                               "userPassword", "gidNumber", "member",
                               "ipaUniqueID", "ipaNTSecurityIdentifier", "modifyTimestamp",
                               "entryUSN", "ipaExternalMember"])
        
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=accounts,{domain_dn}",
                 scope=SCOPE,
                 search_filter=f"(&(gidNumber={gidNumber})(|(objectClass=ipaUserGroup)(objectClass=posixGroup))(cn=*)(&(gidNumber=*)(!(gidNumber=0))))",
                 attrlist=["objectClass", "cn","userPassword", "gidNumber", "member", "ipaUniqueID", "ipaNTSecurityIdentifier", "modifyTimestamp", "entryUSN", "ipaExternalMember"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=accounts,{domain_dn}",
                 scope=SCOPE,
                 search_filter=f"(&(objectClass=ipaHost)(fqdn={host}.{domain}))",
                 attrlist=["objectClass", "cn", "fqdn", "serverHostname", "ipaSshPubKey", "ipaUniqueID"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"fqdn={host}.{domain},cn=computers,cn=accounts,{domain_dn}",
                 scope=SCOPE,
                 search_filter="(objectClass=*)",
                 attrlist=["objectClass", "cn", "memberOf", "ipaUniqueID"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=hbac,{domain_dn}",
                 scope=SCOPE,
                 search_filter="(objectClass=ipaHBACService)",
                 attrlist=["objectClass", "cn", "memberOf", "ipaUniqueID", "member"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=hbac,{domain_dn}",
                 scope=SCOPE,
                 search_filter=f"(&(objectclass=ipaHBACRule)(ipaenabledflag=TRUE)(accessRuleType=allow)(|(hostCategory=all)(memberHost={host}.{domain},cn=computers,cn=accounts,{domain_dn})(memberHost=cn=ipaservers,cn=hostgroups,cn=accounts,{domain_dn})(memberHost=cn=Replication\20Administrators,cn=privileges,cn=pbac,{domain_dn})(memberHost=cn=Add\20Replication\20Agreements,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=Modify\20Replication\20Agreements,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=Read\20Replication\20Agreements,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=Remove\20Replication\20Agreements,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=Modify\20DNA\20Range,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=Read\20PassSync\20Managers\20Configuration,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=Read\20Replication\20Changelog\20Configuration,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=Write\20Replication\20Changelog\20Configuration,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=Modify\20PassSync\20Managers\20Configuration,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=Read\20LDBM\20Database\20Configuration,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=Add\20Configuration\20Sub-Entries,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=Read\20DNA\20Range,cn=permissions,cn=pbac,{domain_dn})(memberHost=cn=adtrust\20agents,cn=sysaccounts,cn=etc,{domain_dn})(memberHost=cn=ADTrust\20Agents,cn=privileges,cn=pbac,{domain_dn})(memberHost=cn=System:\20Read\20system\20trust\20accounts,cn=permissions,cn=pbac,{domain_dn})))",
                 attrlist=["objectClass", "cn", "ipauniqueid", "ipaenabledflag", "accessRuleType", "memberUser", "userCategory",
                           "memberService", "serviceCategory", "sourceHost", "sourceHostCategory", "externalHost", "memberHost", "hostCategory"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=etc,{domain_dn}",
                 scope=SCOPE,
                 search_filter="(&(cn=ipaConfig)(objectClass=ipaGuiConfig))",
                 attrlist=["ipaMigrationEnabled", "ipaSELinuxUserMapDefault", "ipaSELinuxUserMapOrder"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=selinux,{domain_dn}",
                 scope=SCOPE,
                 search_filter="(&(objectclass=ipaselinuxusermap)(ipaEnabledFlag=TRUE))",
                 attrlist=["objectClass", "cn", "memberUser", "memberHost" ,"seeAlso" ,"ipaSELinuxUser", "ipaEnabledFlag", "userCategory", "hostCategory", "ipaUniqueID"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=accounts,{domain_dn}",
                 scope=SCOPE,
                 search_filter="(&(uid=fly-dm)(objectclass=posixAccount)(uid=*)(&(uidNumber=*)(!(uidNumber=0))))",
                 attrlist=["objectClass", "uid", "userPassword",
                           "uidNumber" ,"gidNumber" ,"gecos",
                           "homeDirectory", "loginShell", "krbPrincipalName",
                           "cn", "memberOf" ,"ipaUniqueID", "ipaNTSecurityIdentifier",
                           "modifyTimestamp", "entryUSN", "x-ald-user-caps", "x-ald-user-mac", "x-ald-user-mic-level", "shadowLastChange",
                           "shadowMin", "shadowMax", "shadowWarning", "shadowInactive", "shadowExpire", "shadowFlag",
                           "krbLastPwdChange", "krbPasswordExpiration",
                           "authorizedService",
                           "nsAccountLock", "host", "ipaSshPubKey", "ipaUserAuthType", "userCertificate", "mail"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=trusts,{domain_dn}",
                 scope=SCOPE,
                 search_filter="(&(&(uid=fly-dm)(objectclass=posixAccount)(uid=*)(&(uidNumber=*)(!(uidNumber=0))))(objectClass=ipaIDObject))",
                 attrlist=["objectClass", "uid", "userPassword", "uidNumber" ,"gidNumber" ,"gecos",
                           "homeDirectory", "loginShell", "krbPrincipalName", "cn", "memberOf" ,"ipaUniqueID", "ipaNTSecurityIdentifier",
                           "modifyTimestamp", "entryUSN", "x-ald-user-caps", "x-ald-user-mac", "x-ald-user-mic-level", "shadowLastChange",
                           "shadowMin", "shadowMax", "shadowWarning", "shadowInactive", "shadowExpire", "shadowFlag",
                           "krbLastPwdChange", "krbPasswordExpiration",
                           "authorizedService",
                           "nsAccountLock", "host", "ipaSshPubKey", "ipaUserAuthType", "userCertificate", "mail"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=ranges,cn=etc,{domain_dn}",
                 scope=SCOPE,
                 search_filter="(objectclass=ipaIDRange)",
                 attrlist=["objectClass", "cn", "ipaBaseID",
                           "ipaBaseRID" ,"ipaSecondaryBaseRID" ,"ipaIDRangeSize",
                           "ipaNTTrustedDomainSID", "ipaRangeType"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=certmap,cn=etc,{domain_dn}",
                 scope=SCOPE,
                 search_filter="(|(&(objectClass=ipaCertMapRule)(ipaEnabledFlag=TRUE))(objectClass=ipaCertMapConfigObject))",
                 attrlist=["objectClass", "cn", "ipaCertMapMapRule",
                           "ipaCertMapMatchRule" ,"ipaCertMapPriority" ,"associatedDomain",
                           "ipaCertMapPromptUserName"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=trusts,{domain_dn}",
                 scope=SCOPE,
                 search_filter="(objectclass=ipaNTTrustedDomain)",
                 attrlist=["cn", "ipaNTFlatName", "ipaNTTrustedDomainSID",
                           "ipaNTTrustDirection" ,"ipaNTAdditionalSuffixes" ,"ipaNTSIDBlacklistIncoming"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=default,cn=views,cn=accounts,{domain_dn}",
                 scope=SCOPE,
                 search_filter="(objectclass=*)",
                 attrlist=["ipaDomainResolutionOrder"])

    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=etc,{domain_dn}",
                 scope=SCOPE,
                 search_filter="(&(cn=ipaConfig)(objectClass=ipaGuiConfig))",
                 attrlist=["ipaDomainResolutionOrder"])
    
    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=devices,cn=dev-services,cn=parsec-configs,cn=etc,{domain_dn}",
                 scope=SCOPE,
                 search_filter=f"(&(x-ald-devac-owner=user{user_id})(objectclass=x-ald-device)(x-ald-devac-status=TRUE))")
    
    for group in groups:
        # print(group.decode("utf-8").split(',')[0])
        _ldap_search(ldap_obj=ldap_obj,
                     base=f"cn=devices,cn=dev-services,cn=parsec-configs,cn=etc,{domain_dn}",
                     scope=SCOPE,
                     search_filter=f"(&(x-ald-devac-group={group.decode('utf-8').split(',')[0].split('=')[1]})(objectclass=x-ald-device)(x-ald-devac-status=TRUE))",
                     attrlist=["ipaDomainResolutionOrder", "objectClass", "cn",
                               "x-ald-devac-status", "x-ald-devac-owner", "x-ald-devac-group",
                               "x-ald-devac-attr", "description", "x-ald-devac-rule", "x-ald-devac-mode",
                               "x-ald-devac-mac", "x-ald-devac-aud", "x-ald-devac-mac-type", "entryUSN"])

    _ldap_search(ldap_obj=ldap_obj,
                 base=f"cn=audit-policies,cn=parsec-configs,cn=etc,{domain_dn}",
                 scope=SCOPE,
                 search_filter=f"(&(cn=user:user{user_id})(objectclass=x-ald-audit-policy))",
                 attrlist=["objectClass", "cn", "x-ald-aud-type", "x-ald-aud-mask", "entryUSN"])
    
    for group in groups:
        _ldap_search(ldap_obj=ldap_obj,
                     base=f"cn=devices,cn=dev-services,cn=parsec-configs,cn=etc,{domain_dn}",
                     scope=SCOPE,
                     search_filter=f"(&(cn=group:{group.decode('utf-8').split(',')[0].split('=')[1]})(objectclass=x-ald-audit-policy))")

def auth_and_autorize(**kwargs):
    id_user = kwargs['id_user']
    # print(f"Процесс {id_user} запущен")

    if kwargs['barrier']:
        kwargs["barrier"].wait()
        # print(f"Процесс {id_user} ПРЕОДОЛЕЛ Барьер")

    time_start_single_auth = time.time()
    
    ld_obj = auth(user_id=id_user, array_for_ldap_error=kwargs['array_errors'])
    if ld_obj:
        # pass
        autorize(ldap_obj=ld_obj, user_id=id_user)
        auth_out(ldap_obj=ld_obj)

    time_end_singe_auth = float(time.time() - time_start_single_auth)
    
    arr = kwargs['array']
    arr[id_user] = time_end_singe_auth

    if id_user == kwargs['user_count'] - 5:
        last = kwargs['last']
        last.value = time_end_singe_auth


if __name__ == "__main__":

    f = open("ipa_report.txt", "w")
    f.close()

    f = open("ipa_report_error.txt", 'w')
    f.close()

    for user_count in range(500, 3500, 500):
        barr = Barrier(user_count)
        array = Array("d", user_count)
        value_for_last_proc_delay = Value("d")
        manager = Manager()
        array_errors = manager.dict()

        processes = [Process(target=auth_and_autorize, 
                             kwargs={'id_user': id_user, 
                                     'barrier': barr, 
                                     'array': array, 
                                     'last': value_for_last_proc_delay,
                                     'array_errors': array_errors, 
                                     'user_count': user_count}) 
                                     for id_user in range(0, user_count)
                                     ]
        for p in processes:
            p.start()

        for p in processes:
            p.join()

        sr_znach = sum(list(array)) / len(list(array))
        max_znach = max(list(array))
        min_znach = min(list(array))

        count_error = 0
        for error in list(array_errors.values()):
            if error:
                count_error += 1
        # TODO вспомнить почему user_count + 1
        proc_errors = count_error * 100 / (user_count + 1)

        with open("ipa_report.txt", 'a') as report_file:
            report_file.write(f"{user_count} {proc_errors} {sr_znach} {value_for_last_proc_delay.value} {min_znach} {max_znach}\n")
        
        with open("ipa_report_error.txt", 'a') as error_file:
            error_file.write(f"{'*' * 20}\nИтерация {user_count}\n{array_errors}\n")

        # time.sleep(300)


    # auth_and_autorize(10)