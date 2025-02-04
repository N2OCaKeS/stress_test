FROM python:3.12

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir flask gunicorn paramiko

CMD ["gunicorn", "-b", "0.0.0.0:5002", "wsgi:app", "--timeout", "300"]