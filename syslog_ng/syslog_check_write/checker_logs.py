import time
import datetime
import threading

from conf import QTY_HOURS_CHECK
from generator_logs import generate_log

def check_logs_for_last_hour_with_message(log_file_path, message):
    now = datetime.datetime.now()
    one_hour_ago = now - datetime.timedelta(hours=1)

    logs_for_last_hour = []
    message_found = False

    with open(log_file_path, 'r') as log_file:
        for line in log_file:
            # print(line)
            try:
                timestamp_str = line.split(': ')[1].split(' - ')[0]
                # print(timestamp_str)
                log_timestamp = datetime.datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                # print(log_timestamp)
                if log_timestamp >= one_hour_ago:
                    logs_for_last_hour.append(line.strip())
                    if message in line:
                        message_found = True
            except ValueError:
                continue  # Пропуск строки, если форматирование логов отличается

    return logs_for_last_hour, message_found


def checker():
    log_file_path = '/var/log/syslog'  # Замените на путь к вашему лог-файлу
    message = 'MESSAGE FOR LOG'
    logs, message_found = check_logs_for_last_hour_with_message(log_file_path, message)
    time.sleep(3600)
    if message_found:
        print(f"Найдены сгенерированные логи за последний час, содержащие сообщение '{message}':")
        # for log in logs:
            # print(log)
        return True
    else:
        print(f"Сообщение '{message}' не найдено в логах за последний час.")
        return False
    

def run_checker():
    max_runs = QTY_HOURS_CHECK
    message = "TEST PASSED"
    for i in range(1, max_runs + 1):
        result = checker()
        if not result:
            message = f"TEST FAILED by {i} hour"
            break
        time.sleep(3600)
    return message


if __name__ == "__main__":
    log_gen_tread = threading.Thread(target=generate_log)
    log_gen_tread.start()
    with open("status.txt", "w") as status_file:
        status_file.write("TEST STARTED")
    message = run_checker()
    with open("status.txt", "w") as status_file:
        status_file.write(f"{message}")