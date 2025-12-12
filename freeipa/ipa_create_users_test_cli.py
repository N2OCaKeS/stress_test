import urllib3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue
import os
import subprocess
from ipa_conf import USER_CREATE_START, USER_CREATE_MAX, USER_CREATE_STEP

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SERVER = "virtual-station1.stress-testing.local"
MAX_WORKERS = os.cpu_count()

results_queue = queue.Queue()
result_queue_error = queue.Queue()

def create_user(user_id):
    login = f"user{user_id}"
    ipa_cli_create_user_command = subprocess.run(f"ipa user-add {login} --first=Test{user_id} --last=Testov{user_id}", shell=True, stdout=subprocess.DEVNULL)
    if ipa_cli_create_user_command.returncode == 0:
        return True
    else:
        print(f"Возникла ошибка при создании пользователя. {ipa_cli_create_user_command.returncode}")
        return False
    
def delete_user(user_id):
    login = f"user{user_id}"
    ipa_cli_delete_user_command = subprocess.run(f"ipa user-del {login}", shell=True)
    if ipa_cli_delete_user_command.returncode == 0:
        return True
    else:
        print(f"Возникала ошибка при удалении пользователя {login} - {ipa_cli_delete_user_command.returncode}")
        return False

def main():
    subprocess.run("echo 12345678 | kinit admin", shell=True)
    for user_count in range(USER_CREATE_START, USER_CREATE_MAX + USER_CREATE_STEP, USER_CREATE_STEP):
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
        
        print("Удаление пользователей...")
        for user_id in range(user_count):
            delete_user(user_id=user_id)
       

if __name__ == "__main__":
    main()