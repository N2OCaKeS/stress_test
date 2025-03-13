FROM python:3.9-slim

# Установка devpi-server, devpi-web и devpi-client
RUN pip install --no-cache-dir devpi-server devpi-web devpi-client

# Открываем порт 3141
EXPOSE 3141

# Создаем директорию для данных
RUN mkdir -p /data

# Инициализация devpi-server
RUN devpi-init --serverdir /data

# Устанавливаем переменные окружения для devpi-client
ENV DEVPISERVER_SERVERDIR=/data

# Запускаем devpi-server и настраиваем его
CMD ["sh", "-c", "devpi-server --host 0.0.0.0 --port 3141 --serverdir /data & sleep 5 --mirror-cache-ttl=1 && \
     devpi use http://localhost:3141 && \
     devpi user -c user password=team13 && \
     devpi login user --password=team13 && \
     devpi index -c astra volatile=True && \
     tail -f /dev/null"]