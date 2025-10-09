from python_freeipa import ClientMeta
from python_freeipa.exceptions import FreeIPAError
import urllib3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue
import os

from ipa_conf import USER_CREATE_START, USER_CREATE_MAX, USER_CREATE_STEP

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SERVER = "virtual-station1.stress-testing.local"
MAX_WORKERS = os.cpu_count()

client = ClientMeta(SERVER, verify_ssl=False)
# client.login_kerberos()
client.login("admin", "12345678")

results_queue = queue.Queue()

def create_user(i, client_inst):
    login = f"user{i}"
    givenname = f"Test{i}"
    sn = f"User{i}"
    cn = f"{givenname} {sn}"

    try:
        start_time = time.time()
        client_inst.user_add(
            login,
            givenname,
            sn,
            cn,
            o_userpassword="Test1234!",
            o_loginshell="/bin/bash"
        )
        
        end_time = time.time()
        elapsed = end_time - start_time
        # print(f"Создан {login} за {elapsed} секунд")
        results_queue.put((login, elapsed, True))
        
    except FreeIPAError as e:
        print(f"Ошибка создания {login}: {e}")
        results_queue.put((login, 0, False))

def del_user(user_id):
    try:
        login = f"user{user_id}"
        client.user_del(login)
        # print(f"Пользователь {login} успешно удален")
        # return (username, True)
    except FreeIPAError as e:
        print(f"Ошибка удаления {login}: {e}")
        # return (username, False)

def main():
    for user_count in range(USER_CREATE_START, USER_CREATE_MAX, USER_CREATE_STEP):
        start_total = time.time()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_user = {
                executor.submit(create_user, i, client): i for i in range(user_count)
            }
            
            for future in as_completed(future_to_user):
                user_id = future_to_user[future]
                try:
                    future.result()
                except Exception as exc:
                    print(f'Пользователь {user_id} сгенерировал исключение: {exc}')
        
        end_total = time.time()
        total_time = end_total - start_total
        
        elapsed_times = []
        successful_users = 0
        
        while not results_queue.empty():
            login, elapsed, success = results_queue.get()
            if success:
                elapsed_times.append(elapsed)
                successful_users += 1
        
        average_time_per_user = sum(elapsed_times) / successful_users if successful_users > 0 else 0
        print(successful_users)
        print(total_time)
        print(average_time_per_user)
        with open("ipa_report.txt", 'a') as report_file:
            report_file.write(f"{user_count} {successful_users} {total_time} {average_time_per_user}\n")
        
        print("Удаление пользователей...")
        for user_id in range(USER_CREATE_START, user_count):
            del_user(user_id=user_id)
       

if __name__ == "__main__":
    main()