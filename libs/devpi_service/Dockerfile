FROM python:3.9-slim

# Установка devpi-server, devpi-web (для веб-интерфейса) и devpi-client
RUN pip install --no-cache-dir devpi-server devpi-web devpi-client

# Открываем порт 3141 (используется как для API, так и для веб-интерфейса)
EXPOSE 3141

# Создаем директорию для данных
RUN mkdir -p /data

# Инициализация devpi-сервера
RUN devpi-init --serverdir /data

# Устанавливаем переменную окружения для использования devpi-client
ENV DEVPISERVER_SERVERDIR=/data

# Запускаем devpi-сервер в фоне, проводим начальную настройку и завершаем его
RUN (devpi-server --host 0.0.0.0 --port 3141 --serverdir /data &) && \
    sleep 15 && \
    devpi use http://localhost:3141 && \
    devpi user -c user password=team13 && \
    devpi login user --password=team13 && \
    devpi index -c astra volatile=True 

# При старте контейнера запускается devpi-сервер с включенным веб-интерфейсом (доступным по http://localhost:3141)
CMD ["devpi-server", "--host", "0.0.0.0", "--port", "3141", "--serverdir", "/data"]
