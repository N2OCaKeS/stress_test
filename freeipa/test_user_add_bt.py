from python_freeipa import ClientMeta
from python_freeipa.exceptions import FreeIPAError
import urllib3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue
import os

# from ipa_conf import USER_CREATE_START, USER_CREATE_MAX, USER_CREATE_STEP

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SERVER = "lowserver.stress-testing.local"
MAX_WORKERS = os.cpu_count()

# client = ClientMeta(SERVER, verify_ssl=False)
# client.login_kerberos()
# client.login("admin", "12345678")

client_pool = queue.Queue(maxsize=50)
pool_lock = threading.Lock()

def create_client():
    client = ClientMeta(SERVER, verify_ssl=False)
    for _ in range(3):
        try:
            client.login("admin", "12345678")
            return client
        except Exception as e:
            print(f"Ошибка логина при создании клиента: {e}")
            time.sleep(1)
    raise RuntimeError("Не удалось создать клиент для пула")

def init_client_pool(size=25):
    print(f"Создаём пул из {size} клиентов FreeIPA...")
    for i in range(size):
        client = create_client()
        client_pool.put(client)
    print("Пул готов")

def get_client():
    while True:
        client = client_pool.get()
        try:
            client.ping()
            return client
        except:
            print("Сессия умерла — заменяем клиент в пуле")
            try:
                new_client = create_client()
                client_pool.put(new_client)
            except:
                time.sleep(0.5)
                continue
            return new_client

def return_client(client):
    try:
        client_pool.put(client, timeout=1)
    except queue.Full:
        pass

results_queue = queue.Queue()
result_queue_error = queue.Queue()

def create_user(i):
    login = f"user{i}"
    givenname = f"Test{i}"
    sn = f"User{i}"
    cn = f"{givenname} {sn}"

    client = get_client()
    try:
        for attempt in range(8):
            try:
                start = time.time()
                client.user_add(
                    login, givenname, sn, cn,
                    o_userpassword="Test1234!",
                    o_loginshell="/bin/bash"
                )
                elapsed = time.time() - start
                results_queue.put((login, elapsed, True))
                if i <= 10000:
                    client.group_add_member(
                        "gr_1",
                        o_user=[login]
                    )
                return

            except FreeIPAError as e:
                msg = str(e).lower()
                if "uniqueness" in msg or "operations error" in msg:
                    time.sleep(0.2 * (2 ** attempt))
                    continue
                else:
                    raise

        results_queue.put((login, 0, False))
        result_queue_error.put((login, "max retries exceeded"))

    except Exception as e:
        print(f"Критическая ошибка {login}: {e}")
        results_queue.put((login, 0, False))
        result_queue_error.put((login, e))

    finally:
        return_client(client)

def del_user(user_id):
    try:
        login = f"user{user_id}"
        client = get_client()
        client.user_del(login)
    except FreeIPAError as e:
        print(f"Ошибка удаления {login}: {e}")
        result_queue_error.put((login, e))
    finally:
        return_client(client)

def main():
    init_client_pool(size=30)

    # USER_CREATE_START = 20000
    # USER_CREATE_STEP = 0
    # USER_CREATE_MAX = 20000
    # for user_count in range(USER_CREATE_START, USER_CREATE_MAX + USER_CREATE_STEP, USER_CREATE_STEP):
    user_count = 100000
    start_total = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_user = {
            executor.submit(create_user, i): i for i in range(user_count)
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

    tmpf = open("ipa_report_error.txt", "w")
    tmpf.close()
    while not result_queue_error.empty():
        login, error = result_queue_error.get()
        with open("ipa_report_error.txt", 'a') as error_file:
            error_file.write(f"{login}: {error}\n")
    
    # print("Удаление пользователей...")
    # for user_id in range(user_count):
    #     del_user(user_id=user_id)
       

if __name__ == "__main__":
    main()