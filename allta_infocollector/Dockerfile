#FROM python:3.12
FROM allta.devos.astralinux.ru:21503/python:3.12

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir flask gunicorn paramiko flask-core flask-cors requests

CMD ["gunicorn", "-b", "0.0.0.0:5002", "wsgi:app", "--timeout", "300"]