import smtplib
import threading
import time
from email.mime.text import MimeText

def send_email(server, port, username_to, username_from, password, count):
    for i in range(count):
        try:
            with smtplib.SMTP(server, port) as smtp:
                smtp.login(username_from, password)
                msg = MimeText(f"Test email {i}")
                msg['Subject'] = f"Load Test {i}"
                msg['From'] = username_from
                msg['To'] = username_to
                smtp.send_message(msg)
        except Exception as e:
            print(f"Error: {e}")

threads = []
for _ in range(10):
    t = threading.Thread(target=send_email, args=('localhost', ..., ..., ..., ...,))
    threads.append(t)
    t.start()

for t in threads:
    t.join()