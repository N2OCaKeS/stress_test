#!/home/u/python/Python-3.12.1/venv/bin/python3.12
import sys
import logging
import certifi
import requests
import datetime
import os
import re
import ssl
import threading
import socket

from pathlib import Path
from logging.handlers import RotatingFileHandler
from time import sleep

from allta_front import app



app.secret_key = 'srv_2413'
log_file_dir = '/home/u/git/stress_test/allta_app/'
log_file_name = 'error.log'
log_size = 52428800 



def trust_api_cert_for_requests(host: str = "allta.devos.astralinux.ru", port: int = 21500) -> None:
    certs_dir = Path.home() / ".config" / "allta" / "certs"
    certs_dir.mkdir(parents=True, exist_ok=True)
    cert_file = certs_dir / "allta-api.crt"
    bundle_file = certs_dir / "certifi-allta-bundle.pem"

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert_der = tls.getpeercert(binary_form=True)
        if not cert_der:
            raise RuntimeError("empty peer cert")
        cert_pem = ssl.DER_cert_to_PEM_cert(cert_der).strip() + "\n"
        cert_file.write_text(cert_pem, encoding="utf-8")
    except Exception:
        if not cert_file.exists():
            raise
        cert_pem = cert_file.read_text(encoding="utf-8")

    base = Path(certifi.where()).read_text(encoding="utf-8")
    if not base.endswith("\n"):
        base += "\n"
    bundle_file.write_text(base + cert_pem, encoding="utf-8")

    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle_file)
    os.environ["SSL_CERT_FILE"] = str(bundle_file)


def logrotate():
    logger = logging.getLogger() 
    handler = logger.handlers[0]
    log_version = 1

    while True:
        files = os.listdir(log_file_dir)
        logs_versions = [f for f in files if re.match(r'error(\.\d+)?\.log', f)]
        if logs_versions:
            versions = [int(re.search(r'\d+', f).group()) if re.search(r'\d+', f) else 0 for f in logs_versions]
            log_version = max(versions) + 1

        if os.path.isfile(log_file_dir + log_file_name):
            if os.path.getsize(log_file_dir + log_file_name) >= log_size:
                if not os.path.isfile(f'{log_file_dir}error.{log_version}.log'):
                    os.rename(log_file_dir + log_file_name, f'{log_file_dir}error.{log_version}.log')
                    with open(log_file_dir + log_file_name, 'w') as wlog:
                        wlog.write(f'Start new log {datetime.datetime.now()}')
                    handler.close()
                    new_handler = logging.FileHandler(log_file_dir + log_file_name)
                    new_handler.setFormatter(handler.formatter) 
                    logger.handlers = []
                    logger.addHandler(new_handler)
            else: sleep(3600)

logging.basicConfig(filename=log_file_dir + log_file_name,
                    format='%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s',
                    datefmt='%Y/%m/%d %H:%M:%S',
                    filemode='a')
sys.path.insert(0,"/home/u/git/stress_test/allta_app")


def run_app():
    trust_api_cert_for_requests()
    print(requests.get("https://allta.devos.astralinux.ru:21501/api/server/health").status_code)
    app.run(threaded=True)


task1 = threading.Thread(target=logrotate, daemon=True)
task2 = threading.Thread(target=run_app, daemon=True)    



if __name__ == '__main__':
    task1.start()
    task2.start()