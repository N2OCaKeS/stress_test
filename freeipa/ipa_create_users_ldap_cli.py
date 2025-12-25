import uuid
import time
import os
import subprocess

LDAP_SERVER = "ldap://virtual-station2.stress-testing.local"
BIND_DN = "cn=directory manager"
BIND_PASSWORD = "12345678"
DOMAIN_DN = "dc=stress-testing,dc=local"
GROUP_DN = f"cn=ipausers,cn=groups,cn=accounts,{DOMAIN_DN}"

STEP_U = 4000
REPORT_FILE = "ipa_report.txt"
ERROR_REPORT = "ipa_report_error.txt"

def generate_iteration_ldif(start_id, end_id, iter_num):
    user_filename = f"users_iter_{iter_num}.ldif"
    group_filename = f"group_iter_{iter_num}.ldif"
    
    with open(user_filename, 'w') as u_f, open(group_filename, 'w') as g_f:
        # Настройка заголовка для файла группы (одна модификация на много записей)
        g_f.write(f"dn: {GROUP_DN}\nchangetype: modify\nadd: member\n")
        
        for i in range(start_id, end_id + 1):
            username = f"test{i}"
            uid_gid = str(17001234 + i)
            u_dn = f"uid={username},cn=users,cn=accounts,{DOMAIN_DN}"
            
            # Формируем запись пользователя
            u_f.write(f"dn: {u_dn}\n")
            u_f.write("objectClass: top\nobjectClass: person\nobjectClass: organizationalperson\n")
            u_f.write("objectClass: inetorgperson\nobjectClass: inetuser\nobjectClass: posixaccount\n")
            u_f.write("objectClass: krbprincipalaux\nobjectClass: krbticketpolicyaux\n")
            u_f.write("objectClass: ipaobject\nobjectClass: ipasshuser\n")
            u_f.write("objectClass: x-ald-user\nobjectClass: x-ald-user-parsec14\nobjectClass: x-ald-audit-policy\n")
            u_f.write(f"uid: {username}\n")
            u_f.write(f"givenName: Test\n")
            u_f.write(f"sn: Testov{i}\n")
            u_f.write(f"cn: Test Testov{i}\n")
            u_f.write(f"displayName: Test Testov{i}\n")
            u_f.write(f"initials: TT\n")
            u_f.write(f"gecos: Test Testov{i}\n")
            u_f.write(f"krbPrincipalName: {username}@STRESS-TESTING.LOCAL\n")
            u_f.write(f"loginShell: /bin/bash\n")
            u_f.write(f"homeDirectory: /home/{username}@stress-testing.local\n")
            u_f.write(f"mail: {username}@stress-testing.local\n")
            u_f.write(f"uidNumber: {uid_gid}\n")
            u_f.write(f"gidNumber: {uid_gid}\n")
            u_f.write("xaldusermacmax: 0\nxaldusermacmin: 0\nx-ald-user-mac: 0:0x0:0:0x0\n")
            u_f.write(f"ipaUniqueID: {uuid.uuid4()}\n\n")
            
            #  Формируем запись для группы
            g_f.write(f"member: {u_dn}\n")
            
    return user_filename, group_filename

def run_ldapadd(filename):
    # -x (simple auth), -c (ignore errors), -H (host), -D (binddn), -w (passwd)
    cmd = [
        "ldapadd", "-x", "-c", 
        "-H", LDAP_SERVER, 
        "-D", BIND_DN, 
        "-w", BIND_PASSWORD, 
        "-f", filename
    ]
    
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate()
    return stdout, stderr

if __name__ == "__main__":
    start_id = 1
    end_id = 4000
    iteration = 1
    
    open(REPORT_FILE, 'w').close()
    open(ERROR_REPORT, 'w').close()

    print(f"{'Итерация':<5} | {'Кол-во':<7} | {'Время (сек)':<12} | {'User/Sec':<10} | {'Ошибки'}")
    print("-" * 70)

    while iteration <= 5:
        count = end_id - start_id + 1
        
        u_file, g_file = generate_iteration_ldif(start_id, end_id, iteration)
        
        start_time = time.time()
        
        out_u, err_u = run_ldapadd(u_file)
        out_g, err_g = run_ldapadd(g_file)
        
        duration = time.time() - start_time
        speed = count / duration if duration > 0 else 0

        err_lines = [l for l in (err_u + err_g).split('\n') if "ldap_add" in l and "Already exists" not in l]
        fail_count = len(err_lines)

        print(f"{iteration:<9} | {count:<7} | {int(duration):<12} | {int(speed):<10} | {fail_count}")

        with open(REPORT_FILE, 'a') as f:
            f.write(f"{count} {count - fail_count} {int(duration)} {int(speed)}\n")
        
        if fail_count > 0:
            with open(ERROR_REPORT, 'a') as f:
                f.write(f"--- Iteration {iteration} ---\n{err_u}\n{err_g}\n")

        iteration += 1
        start_id = end_id + 1
        end_id = end_id + (STEP_U * iteration)

    print("-" * 70)
    print(f"Тестирование завершено.")