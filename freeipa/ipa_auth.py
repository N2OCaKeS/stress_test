import ldap
import time
from multiprocessing import Process, Barrier, Array, Value, Manager


def auth(user_id, errors={}, barrier=None, arr=None, last=None):
    # print(f"Процесс {user_id} запущен")
    if barrier:
        barrier.wait()
    # print(f"Процесс {user_id} ПРЕОДОЛЕЛ Барьер")
    time_start_single_auth = time.time()
    try:
        l = ldap.initialize("ldap://stand-1-i711700-32-low.stress-testing.local")
        
        l.protocol_version = ldap.VERSION3
        username = f"uid=user{user_id},cn=users,cn=compat,dc=stress-testing,dc=local" # введите DN (Distinguished Name) пользователя
        password  = "password" # введите пароль пользователя

        l.simple_bind_s(username, password)
    except ldap.INVALID_CREDENTIALS as e:
        # print("Your username or password is incorrect.")
        errors[f'{user_id}'] = e
    except ldap.LDAPError as e:
        errors[f'{user_id}'] = e
    # finally:
    #     l.unbind_s()

    
    # Вывод информации о пользователе
    whoami = l.whoami_s()
    time_end_singe_auth = float(time.time() - time_start_single_auth)
    
    if arr:
        arr[user_id] = time_end_singe_auth
    if user_id == USER_COUNT - 5:
        last.value = time_end_singe_auth


if __name__ == '__main__':
    time_start = time.time()
    
    f = open("ipa_report.txt", "w")
    f.close()

    f = open("ipa_report_error.txt", 'w')
    f.close()

    for USER_COUNT in range(1000, 6000, 1000):
    # for USER_COUNT in [19000, 20000]:
        barr = Barrier(USER_COUNT - 1)
        array = Array("d", USER_COUNT)
        value_for_last_proc_delay = Value("d")
        manager = Manager()
        array_errors = manager.dict()
    
        processes = [Process(target=auth, args=(id_user, array_errors, barr, array, value_for_last_proc_delay)) for id_user in range(1, USER_COUNT)]
        for p in processes:
            p.start()

        for p in processes:
            p.join()

        time_exec = time.time() - time_start
        sr_znach = sum(list(array)) / len(list(array))
        max_znach = max(list(array))
        min_znach = min(list(array))
        
        count_error = 0
        for error in list(array_errors.values()):
            if error:
                count_error += 1
        
        proc_errors = count_error * 100 / (USER_COUNT + 1)

        print(time_exec)
        print(f"Ср. знач = {sr_znach}")
        print(f"Min = {min_znach}")
        print(f"Max = {max_znach}")
        print(f"Last proc delay = {value_for_last_proc_delay.value}")
        print(f"% Errors = {proc_errors}")

        # print(array_errors)

        with open("ipa_report.txt", 'a') as report_file:
            report_file.write(f"{USER_COUNT} {proc_errors} {sr_znach} {value_for_last_proc_delay.value} {min_znach} {max_znach}\n")

        with open("ipa_report_error.txt", 'a') as error_file:
            error_file.write(f"{'*' * 20}\nИтерация {USER_COUNT}\n{array_errors}\n")