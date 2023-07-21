import psutil
import os
import sys
import time

def fill_disk():
    with open("/fill_disk_test.txt", "w") as f:
        f.write("")
    try:
        while True:
            print(psutil.disk_usage("/").percent)
            with open("/fill_disk_test.txt", "a") as f:
                f.write(f"{'Заполняем жесткий диск' * 2048}")
    except IOError as e:
        print(f"Возникла ошибка: {e}")
    
    print("Тестирование завершено!")
    print(psutil.disk_usage("/"))

if __name__ == "__main__":
    if os.getuid() != 0:
        print("Запускать только от пользователя root!")
        sys.exit(1)

    fill_disk()
    # time.sleep(60)
    # os.system('reboot')
    