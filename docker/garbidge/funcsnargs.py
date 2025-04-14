import subprocess
import psutil
import csv
import time
import matplotlib as plt
import pandas as pd
import argparse
from datetime import datetime
import docker
from threading import Thread
import os
    

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


def html_converter(csv_file, output_dir):
    import pandas as pd
    import os

    df = pd.read_csv(csv_file)

    # Безопасно удаляем строку Aggregated, если колонка 'Name' есть
    if "Name" in df.columns:
        df = df[df["Name"] != "Aggregated"]

    html_table = df.to_html(index=False)

    base_name = os.path.basename(csv_file).replace(".csv", ".html")
    html_path = os.path.join(output_dir, base_name)

    os.makedirs(output_dir, exist_ok=True)
    with open(html_path, 'w') as file:
        file.write(html_table)

    print(f"HTML создан: {html_path}")

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
