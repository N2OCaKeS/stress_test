# allta_app_service — Gunicorn/Flask backend, обрезанный от фронтенда и бота
# (см. emm/obsidian/plans/2026-09-22-allta-app-service-containerize.md)
FROM python:3.12-slim

# libpq-dev/build-essential — сборка psycopg2 из исходников (в req.txt он не -binary);
# libxml2-dev/libxslt1-dev — lxml; sudo/sshpass/openssh-client — нужны реальному
# DRBL/SSH-флоу приложения (libs/liballta.py дергает sudo и sshpass напрямую).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libxml2-dev \
        libxslt1-dev \
        sudo \
        sshpass \
        openssh-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Приложение и логика деплоя на стенды хардкодят абсолютный путь
# /home/u/git/stress_test/allta_app (wsgi.py, backup_image.py, allta_back.py) —
# держим его и как WORKDIR, и как домашний каталог пользователя, под которым
# крутится gunicorn, чтобы ничего в коде не пришлось переписывать.
RUN useradd -m -u 1000 -d /home/u -s /bin/bash u \
    && echo 'u ALL=(ALL) NOPASSWD: /usr/sbin/drbl-ocs' > /etc/sudoers.d/drbl \
    && chmod 0440 /etc/sudoers.d/drbl

WORKDIR /home/u/git/stress_test/allta_app

COPY req.txt .
RUN pip install --no-cache-dir -r req.txt

COPY . .

# telegrambot/ и logs/ — рантайм-каталоги, которые код открывает на запись
# по хардкоду; создаём их заранее, даже если в образе они пустые.
# libs/liballta.py на импорте читает несколько секретных файлов из /home/u
# напрямую (обфусцированные url, SSH-пароли стендов) без обработки
# отсутствия — без них падает импорт целиком и gunicorn не поднимает ни
# одного воркера. На реальном хосте (103.10) эти файлы уже лежат с настоящим
# содержимым; здесь — пустые заглушки только чтобы стартовать. Для прод-
# деплоя эти файлы стоит смонтировать с хоста, а не печь в образ.
# libs/libilo.py больше не читает /home/u/ilo.py — iLO-креды по номеру
# стенда резолвятся через server_service (SERVER_SERVICE_URL,
# ALLTA_APP_SERVICE_BOT_TOKEN, оба опциональны на старте контейнера).
RUN mkdir -p telegrambot logs \
    && touch /home/u/url /home/u/url_mob /home/u/url_brest /home/u/up /home/u/brestp \
    && chown -R u:u /home/u

USER u

# Порт задаётся снаружи (ALLTA_PORT), не зашит в образ — на хосте он делит
# адресное пространство портов с остальными сервисами, конкретное значение
# назначает владелец при деплое.
ENV ALLTA_PORT=8000
EXPOSE ${ALLTA_PORT}

CMD gunicorn --workers 4 -b 0.0.0.0:${ALLTA_PORT} wsgi:app --timeout 3600
