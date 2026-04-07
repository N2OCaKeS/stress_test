import psutil
import datetime
import time

def get_ram_usage():
    memory = psutil.virtual_memory()
    # в ГБ
    return {
        'total': memory.total / (1024 ** 3),
        'available': memory.available / (1024 ** 3),
        'used': memory.used / (1024 ** 3),
        'percent': memory.percent,
        'free': memory.free / (1024 ** 3)
    }


def write_ram_usage_to_file(filename="/home/u/ram_usage_log.txt"):
    try:
        with open(filename, 'a', encoding='utf-8') as file:
            while True:
                ram_usage = get_ram_usage()
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                log_entry = (
                    f"{timestamp} "
                    f"{ram_usage['total']:.2f} "
                    f"{ram_usage['available']:.2f} "
                    f"{ram_usage['used']:.2f} "
                    f"{ram_usage['percent']} "
                    f"{ram_usage['free']:.2f}\n"
                )
                
                file.write(log_entry)
                file.flush()
                
                time.sleep(1)
                
    except Exception as e:
        print(f"Произошла ошибка: {e}")


if __name__ == "__main__":
    write_ram_usage_to_file()