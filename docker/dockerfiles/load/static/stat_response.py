import psutil
import csv
import time
import matplotlib.pyplot as plt
from collections import deque

# Настроим параметры графика
plt.ion()  # Включение интерактивного режима
fig, ax = plt.subplots(figsize=(10, 6))

# Очереди для хранения данных для графика
time_data = deque(maxlen=60)  # Храним 60 последних значений
cpu_data = deque(maxlen=60)  # Храним 60 последних значений
memory_data = deque(maxlen=60)  # Храним 60 последних значений

# Создаем файл для записи данных
with open('system_stats.csv', mode='w', newline='') as file:
    writer = csv.writer(file)
    writer.writerow(['Timestamp', 'Total Memory (MB)', 'Used Memory (MB)', 'Free Memory (MB)', 'CPU Usage (%)'])

    while True:
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        
        # Получаем данные о системе
        memory = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=1)  # Получаем процент использования CPU за 1 секунду
        
        # Добавляем данные в файл CSV
        writer.writerow([timestamp, memory.total / (1024 * 1024), memory.used / (1024 * 1024), memory.free / (1024 * 1024), cpu])
        
        # Добавляем данные в очереди для графика
        time_data.append(timestamp)
        cpu_data.append(cpu)
        memory_data.append(memory.used / (1024 * 1024))  # Переводим в мегабайты

        # Обновляем график
        ax.clear()  # Очищаем старый график
        ax.plot(time_data, cpu_data, label="CPU Usage (%)", color='tab:red')
        ax.plot(time_data, memory_data, label="Memory Usage (MB)", color='tab:blue')

        ax.set_xlabel('Time')
        ax.set_ylabel('Usage')
        ax.set_title('System CPU and Memory Usage')
        ax.legend(loc='upper left')

        # Настройка отображения графика
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        plt.pause(1)  # Пауза, чтобы обновить график раз в секунду

        time.sleep(1)
