import subprocess
import time


def run_scripts():
    print("Запуск copy.sh...")
    copy_process = subprocess.Popen(
        ['bash /home/u/copy_files.sh'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True
    )
    
    time.sleep(1)
    
    print("Запуск get_info.py...")
    info_process = subprocess.Popen(
        ['python3', '/home/u/get_info.py'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    print("Оба скрипта запущены. Ожидание завершения copy.sh...")
    
    copy_returncode = copy_process.wait()
    
    print(f"copy.sh завершился с кодом: {copy_returncode}")
    print("get_info.py продолжит работу еще 60 секунд...")
    
    time.sleep(60)
    
    print("Останавливаем get_info.py...")
    info_process.terminate()
    
    time.sleep(2)
    
    if info_process.poll() is None:
        info_process.kill()
    
    print("Все скрипты завершены!")
    
    print("\n--- Вывод copy.sh ---")
    stdout, stderr = copy_process.communicate()
    print(stdout)
    if stderr:
        print("Ошибки copy.sh:", stderr)
    
    print("\n--- Вывод get_info.py (первые 10 строк) ---")
    stdout, stderr = info_process.communicate()
    lines = stdout.split('\n')
    for line in lines[:10]:
        print(line)
    
if __name__ == "__main__":
    run_scripts()