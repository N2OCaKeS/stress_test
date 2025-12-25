import ldap
import uuid
import time
import ldap.modlist as modlist
from concurrent.futures import ThreadPoolExecutor, as_completed

LDAP_SERVER = "ldap://virtual-station1.stress-testing.local"
BIND_DN = "cn=directory manager"
BIND_PASSWORD = "12345678"
DOMAIN_DN = "dc=stress-testing,dc=local"

def add_ipa_user_minimal(test_id):
    username = f"test{test_id}"
    uid_gid = str(17001234 + test_id)
    
    user_dn = f"uid={username},cn=users,cn=accounts,{DOMAIN_DN}"
    common_group_dn = f"cn=ipausers,cn=groups,cn=accounts,{DOMAIN_DN}"
    
    try:
        l = ldap.initialize(LDAP_SERVER)
        l.protocol_version = ldap.VERSION3
        l.simple_bind_s(BIND_DN, BIND_PASSWORD)

        user_attrs = {
            'objectClass': [
                b'top', b'person', b'organizationalperson', b'inetorgperson',
                b'inetuser', b'posixaccount', b'krbprincipalaux', 
                b'krbticketpolicyaux', b'ipaobject', b'ipasshuser',
                b'x-ald-user', b'x-ald-user-parsec14', b'x-ald-audit-policy'
            ],
            'uid': [username.encode()],
            'givenName': [b'Test'],
            'sn': [f'Testov{test_id}'.encode()],
            'cn': [f'Test Testov{test_id}'.encode()],
            'displayName': [f'Test Testov{test_id}'.encode()],
            'initials': [b'TT'],
            'gecos': [f'Test Testov{test_id}'.encode()],
            'krbPrincipalName': [f"{username}@STRESS-TESTING.LOCAL".encode()],
            'loginShell': [b'/bin/bash'],
            'homeDirectory': [f'/home/{username}@stress-testing.local'.encode()],
            'mail': [f'{username}@stress-testing.local'.encode()],
            'uidNumber': [uid_gid.encode()],
            'gidNumber': [uid_gid.encode()],
            'xaldusermacmax': [b'0'],
            'xaldusermacmin': [b'0'],
            'x-ald-user-mac': [b'0:0x0:0:0x0'],
            'ipaUniqueID': [str(uuid.uuid4()).encode()],
        }

        l.add_s(user_dn, modlist.addModlist(user_attrs))

        try:
            add_member = [(ldap.MOD_ADD, 'member', [user_dn.encode()])]
            l.modify_s(common_group_dn, add_member)
        except ldap.TYPE_OR_VALUE_EXISTS:
            pass
        
        return (True, username, "Создан успешно")
    
    except ldap.ALREADY_EXISTS:
        return (False, username, "Уже существует")
    except ldap.LDAPError as e:
        return (False, username, f"Крит. ошибка: {e}")
    finally:
        if l:
            l.unbind_s()

if __name__ == "__main__":
    """
        1 итерация: 1 - 2000  START_ID=1; END_ID=2000;  2ku
        2 итерация: 1 - 4000  START_ID=1; END_ID=4000;  4ku
        3 итерация: 1 - 6000  START_ID=1; END_ID=6000;  6ku
        4 итерация: 1 - 8000  START_ID=1; END_ID=8000;  8ku
        5 итерация: 1 - 10000 START_ID=1; END_ID=10000; 10ku
                                                        30ku

    ------------------------------------------------------------------

        1 итерация: 1 - 2000.      START_ID=1;     END_ID=2000;  2ku
        2 итерация: 2001 - 6000.   START_ID=2001;  END_ID=6000;  4ku
        3 итерация: 6001 - 12000.  START_ID=6001;  END_ID=12000; 6ku
        4 итерация: 12001 - 20000. START_ID=12001; END_ID=20000; 8ku
        5 итерация: 20001 - 30000. START_ID=20001; END_ID=30000; 10ku
                                                                 30ku
    ------------------------------------------------------------------

        1 итерация: 1-4000.  START_ID=1; END_ID=4000;   4ku
        2 итерация: 1-8000.  START_ID=1; END_ID=8000;   8ku
        3 итерация: 1-12000. START_ID=1; END_ID=12000;  12ku
        4 итерация: 1-16000. START_ID=1; END_ID=16000;  16ku
        5 итерация: 1-20000. START_ID=1; END_ID=20000;  20ku
                                                        60ku

    ------------------------------------------------------------------
        1 итерация: 1 - 4000.      START_ID=1;     END_ID=4000;   4ku
        2 итерация: 4001 - 12000.  START_ID=4001;  END_ID=12000;  8ku
        3 итерация: 12001 - 24000. START_ID=12001; END_ID=24000;  12ku
        4 итерация: 24001 - 40000. START_ID=24001; END_ID=40000;  16ku
        5 итерация: 40001 - 60000. START_ID=40001; END_ID=60000;  20ku
                                                                  60ku

    itaration = 1
    next_number_iteration = iteration + 1
    1) START_ID = 1; END_ID 4000.       STEP_U = 4000
       
       START_ID = END_ID + 1 = 4001
       END_ID = END_ID + (STEP_U * next_number_iteration) = 4000 + (4000 * 2) = 12000
    
    2) START_ID = 4001; END_ID = 12000.  STEP_U = 4000
       
       START_ID = END_ID + 1 = 12001
       END_ID = END_ID + (STEP_U * next_number_iteration) = 12000 + (4000 * 3) = 24000

    3) START_ID = 12001; END_ID = 24000. STEP_U 24000

       START_ID = END_ID + 1 = 24001
       END_ID = END_ID + (STEP_U * next_number_iteration) = 24000 + (4000 * 4) = 40000
    
    4) START_ID = 24001; END_ID = 40000 STEP_U = 4000

       START_ID = END_ID + 1 = 40001
       END_ID = END_ID + (STEP_U * next_number_iteration) = 40000 + (4000 * 5) = 60000

    5) START_ID = 40001; END_ID = 60000 STEP_U = 4000

    ------------------------------------------------------------------
    """
    MAX_WORKERS = 20
    STEP_U = 4000

    start_id = 1
    end_id = 4000
    iteration = 1
    errors = []
    
    while iteration <= 5:
        print(f"Запуск: {end_id - start_id + 1} пользователей в {MAX_WORKERS} потоков.")

        success_count = 0
        fail_count = 0
        exists_count = 0

        start_time = time.time()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_id = {
                executor.submit(add_ipa_user_minimal, i): i 
                for i in range(start_id, end_id + 1)
            }

            for future in as_completed(future_to_id):
                is_success, user, msg = future.result()
                
                if is_success:
                    success_count += 1
                else:
                    if "Уже существует" in msg:
                        exists_count += 1
                    else:
                        fail_count += 1
                        errors.append(f"[FAIL] пользователь {user}: {msg}")
                        print(f"[FAIL] пользователь {user}: {msg}")

                total_processed = success_count + fail_count + exists_count
                if total_processed % 1000 == 0:
                    print(f"Обработано: {total_processed}...")

        duration = time.time() - start_time
        
        print("-" * 30)
        print(f"ИТОГИ ЗА {duration:.2f} сек:")
        print(f"Успешно создано: {success_count}")
        print(f"Уже были: {exists_count}")
        print(f"Ошибок: {fail_count}")
        print(f"Скорость: {((end_id - start_id + 1) / duration):.2f} user/sec")

        with open("ipa_report.txt", 'a') as report_file:
            report_file.write(f"{end_id - start_id + 1} {success_count} {duration} {((end_id - start_id + 1) / duration):.2f}\n")
                
        iteration +=1
        start_id = end_id + 1
        end_id = end_id + (STEP_U * iteration)
    
    with open("ipa_report_error.txt", 'a') as error_file:
        error_file.writelines(errors)