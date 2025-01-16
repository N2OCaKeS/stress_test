import time
import datetime
import threading

from conf import QTY_HOURS_CHECK
from generator_logs import generate_log

def check_logs_for_last_hour_with_message(log_file_path, message):
    now = datetime.datetime.now()
    one_hour_ago = now - datetime.timedelta(hours=1)

    logs_for_last_hour = []

    with open(log_file_path, 'r') as log_file:
        for line in log_file:
            # print(line)
            try:
                # timestamp_str = line.split(': ')[1].split(' - ')[0]
                timestamp_str_lst = line.split(' ')
                timestamp_str = f"{timestamp_str_lst[0]} {timestamp_str_lst[1]} {timestamp_str_lst[2]}"
                # print(timestamp_str)
                current_year = datetime.datetime.now().year
                log_timestamp = datetime.datetime.strptime(timestamp_str, "%b %d %H:%M:%S")
                log_timestamp = log_timestamp.replace(year=current_year)
                # print(log_timestamp)
                if log_timestamp >= one_hour_ago:
                    logs_for_last_hour.append(line.strip())

            except ValueError as err:
                print(f"ОШИБКА: {err}")
        
    message_found = False
    for line in logs_for_last_hour:
        if message in line:
            message_found = True
    
    return logs_for_last_hour, message_found


if __name__ == "__main__":
    with open("status.txt", "w") as status_file:
        status_file.write("TEST STARTED")
    start_time = datetime.datetime.now()
    print(start_time)

    log_gen_tread = threading.Thread(target=generate_log, daemon=True)
    log_gen_tread.start()
    
    for hour in range(QTY_HOURS_CHECK):
    # for hour in range(20):
        time.sleep(3600)
        logs, mess_found = check_logs_for_last_hour_with_message("/var/log/syslog", message="MESSAGE FOR LOG")
        if not mess_found:
            status = f"TEST FAILED by {hour} min"
            break
        else:
            status = "TEST PASSED"

    with open("status.txt", "w") as status_file:
        status_file.write(f"{status}")

    end_time = datetime.datetime.now()
    print(end_time)
