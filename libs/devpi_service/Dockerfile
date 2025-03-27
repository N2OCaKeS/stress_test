FROM python:3.12-slim

WORKDIR /git

# Устанавливаем переменную окружения
ENV DEVPI_USER=${DEVPI_USER}
ENV DEVPI_PASSWORD=${DEVPI_PASSWORD}
ENV DEVPI_ADMIN_PASSWORD=${DEVPI_ADMIN_PASSWORD}
ENV DEVPISERVER_SERVERDIR=/data

# Установка devpi-server, devpi-web (для веб-интерфейса) и devpi-client
RUN pip install --no-cache-dir devpi-server devpi-web devpi-client
RUN apt update && apt install git -y

# Открываем порт 3141 (используется как для API, так и для веб-интерфейса)
EXPOSE 3141

# Создаем директорию для данных
RUN mkdir -p /data

# Инициализация devpi-сервера
RUN devpi-init --serverdir /data --root-passwd ${DEVPI_ADMIN_PASSWORD} 



# Запускаем devpi-сервер в фоне, проводим начальную настройку и завершаем его 

RUN (devpi-server --host 0.0.0.0 --port 3141 --serverdir /data &) && \
    sleep 15 && \
    devpi use http://localhost:3141 && \
    devpi login root --password=${DEVPI_ADMIN_PASSWORD} && \
    devpi index -c astra bases=root/pypi acl_upload=root acl_toxresult_upload=root && \
    devpi user -c ${DEVPI_USER} password=${DEVPI_PASSWORD} && \
    devpi login ${DEVPI_USER} --password=${DEVPI_PASSWORD} && \
    devpi index -c dev bases=root/pypi
RUN kill -TERM 1

COPY ./auto_check_new_version/new_release.py /git/

# При старте контейнера запускается devpi-сервер с включенным веб-интерфейсом (доступным по http://localhost:3141)
CMD ["sh", "-c", "(devpi-server --host 0.0.0.0 --port 3141 --serverdir /data &) && python3 new_release.py"]

