import subprocess
import psutil
import csv
import time
import matplotlib as plt
import pandas as pd
import argparse
import core.variables as var
from datetime import datetime
import docker
from threading import Thread

ram_worker = 1

parser = argparse.ArgumentParser()
parser.add_argument("--test",
                    type=str,
                    choices=["load", "http", "remove", "load-http", "apache"],
                    help="Type your test",
                    dest="TEST_TYPE")
parser.add_argument("-o", "--docker-image",
                    type=str,
                    help="Docker base image for stress testing",
                    default="ubuntu:latest",
                    dest="CONT_NAME")
parser.add_argument("-c", "--count",
                    type=int,
                    help="Choose count of containers",
                    default=2,
                    dest="COUNT")
parser.add_argument("-t", "--timer",
                    type=int,
                    help="timer for test(default 1 min)",
                    default=1,
                    dest="TIMER")
parser.add_argument("-cpu", "--load-cpu",
                    type=int,
                    help="CPU load (default 1 worker)",
                    default=1,
                    dest="LOAD_CPU")
parser.add_argument("-ram", "--load-ram",
                    type=str,
                    help="RAM load (default 256 RAM)",
                    default=256,
                    dest="LOAD_RAM")
parser.add_argument("-u", "--users",
                    type=str,
                    help="Users for http requests.",
                    default=1,
                    dest="USERS")
args = parser.parse_args()


def create_load_docker_compose(content): 
    run_command("docker build -t stress_test_image ./dockerfiles/load/")
    # Создаем нужное количество сервисов из нагрузочного образа
    for i in range(1, args.COUNT + 1):
        service_name = f"stress_{i}"
        service_content = f"""
  {service_name}:
    image: stress_test_image
    container_name: {service_name}
    command: ["stress", "--vm", "1", "--vm-bytes", "{args.LOAD_RAM}", "--timeout", "{args.TIMER * 60}"]
    stdin_open: true
    tty: true
"""
        content += service_content

    with open("docker-compose.yml", 'w') as file:
        file.write(content)

    print("Файл docker-compose.yml успешно создан.")
    return content


def create_http_docker_compose(content):
    run_command("docker build -t apache ./dockerfiles/http/apache-httpd/")
    # run_command("docker build --no-cache -t ab ./dockerfiles/http/apache-bench/")
    content += var.apache_content

    with open("docker-compose.yml", 'w') as file:
        file.write(content)
    
    print("Файл docker-compose.yml успешно создан.")
    return content
    

def run_command(command):
    try:
        result = subprocess.run(command, shell=True, check=True, text=True, capture_output=True)
        print(result.stdout.strip())
        return True
    except subprocess.CalledProcessError as e:
        print("Команда завершилась с ошибкой:")
        print(e)
        return False
    

def collect_system_stats_to_csv(filename="system_stats.csv", duration=args.TIMER):
    """Функция для сбора статистики о системе и записи в CSV"""
    with open("protocols/csv_format/" + filename, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Timestamp', 'Total Memory (MB)', 'Used Memory (MB)', 'Free Memory (MB)', 'CPU Usage (%)'])
        
        # Записываем данные каждую секунду в течение 'duration' секунд
        for i in range(duration*30):
            print(f"{i} запись")
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            memory = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=1)
            
            # Записываем данные в CSV
            writer.writerow([timestamp, memory.total / (1024 * 1024), memory.used / (1024 * 1024), memory.free / (1024 * 1024), cpu])
            time.sleep(1)

    print(f"Данные успешно записаны в {filename}")


def collect_container_stats_to_csv(filename="container_stats.csv", duration=args.TIMER, container_count=args.COUNT):
    """Функция для сбора статистики о контейнерах и записи в CSV"""
    
    print("Начинается сбор данных о контейнерах...")
    client = docker.from_env()

    # Словарь для хранения предыдущих значений CPU и времени
    prev_cpu_usage = {}
    prev_time = {}

    with open("protocols/csv_format/" + filename, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Timestamp', 'Container Name', 'Container Status', 'CPU Usage (%)', 'Memory Usage (MB)', 'Is Running'])
        
        for _ in range(duration * 60):  # Цикл на duration минут (в секундах)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            for i in range(1, container_count + 1):
                container_name = f"stress_{i}"
                try:
                    container = client.containers.get(container_name)
                    status = container.status
                    is_running = status == "running"
                    
                    stats = container.stats(stream=False)

                    # Получаем текущее значение CPU и время
                    cpu_usage = stats['cpu_stats']['cpu_usage']['total_usage']
                    system_cpu_usage = stats['cpu_stats']['system_cpu_usage']
                    current_time = time.time_ns()

                    # Вычисляем процент использования CPU
                    if container_name in prev_cpu_usage:
                        time_delta = current_time - prev_time[container_name]
                        cpu_delta = cpu_usage - prev_cpu_usage[container_name]
                        cpu_percent = (cpu_delta / time_delta) * 100
                    else:
                        cpu_percent = 0.0

                    # Обновляем предыдущие значения
                    prev_cpu_usage[container_name] = cpu_usage
                    prev_time[container_name] = current_time

                    # Получаем использование памяти
                    memory_usage = stats['memory_stats'].get('usage', 0) / (1024 * 1024)

                    # Записываем данные в CSV
                    writer.writerow([timestamp, container_name, status, cpu_percent, memory_usage, is_running])
                    print(f"Собраны данные для {container_name}")

                except docker.errors.NotFound:
                    print(f"Контейнер {container_name} не найден!")
                    continue
            
            time.sleep(1)

    print(f"Данные успешно записаны в {filename}")


def collect_stats_in_thread():
    system_stats_thread = Thread(target=collect_system_stats_to_csv)
    container_stats_thread = Thread(target=collect_container_stats_to_csv)
    
    system_stats_thread.start()
    container_stats_thread.start()
    
    system_stats_thread.join()
    container_stats_thread.join()


def html_converter(csv_file):
    df = pd.read_csv(csv_file)
    html_table = df.to_html(index=False)
    html_file = csv_file.replace(".csv", ".html")

    with open("protocols/html_format/" + html_file, 'w') as file:
        file.write(html_table)

    print("HTML is converted!")


# def plot_system_stats(filename="system_stats.csv"):
#     """Функция для построения графика из CSV"""
#     timestamps = []
#     cpu_usage = []
#     memory_usage = []
    
#     # Чтение данных из CSV
#     with open(filename, mode='r') as file:
#         reader = csv.reader(file)
#         next(reader)  # Пропустить заголовок
#         for row in reader:
#             timestamps.append(row[0])
#             memory_usage.append(float(row[2]))
#             cpu_usage.append(float(row[4]))

#     # Построение графика
#     plt.figure(figsize=(10, 6))
#     plt.plot(timestamps, cpu_usage, label="CPU Usage (%)", color='tab:red')
#     plt.plot(timestamps, memory_usage, label="Memory Usage (MB)", color='tab:blue')
#     plt.xticks(rotation=45, ha='right')
#     plt.xlabel('Timestamp')
#     plt.ylabel('Usage')
#     plt.title('System CPU and Memory Usage Over Time')
#     plt.legend()
#     plt.tight_layout()
#     plt.show()