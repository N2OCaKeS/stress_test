import smtplib
import threading
import time
from email.mime.text import MIMEText
from exb_conf import MAIL_START, MAIL_MAX, MAIL_STEP, MAX_WORKERS
from concurrent.futures import ThreadPoolExecutor, as_completed


class SMTPTest:
    def __init__(self):
        self.email_config = {
            "server": 'localhost',
            "port": '25',
            "username_to": 'test1@test.local',
            "username_from": 'test2@test.local',
            "password": '1',
        }

    def send_single_email(self):
        try:
            with smtplib.SMTP(self.email_config["server"], self.email_config["port"]) as smtp:
                msg = MIMEText(f"Test email {i}")
                msg['Subject'] = f"Load Test {i}"
                msg['From'] = self.email_config["username_from"]
                msg['To'] = self.email_config["username_to"]
                smtp.send_message(msg)
        except Exception as e:
            print(f"Error: {e}")

    def run_test(self):
        results = []
        for current_mail in range(MAIL_START, MAIL_MAX + MAIL_STEP, MAIL_STEP):
            start_time = time.time()
            successful_count = 0
            failed_count = 0
            
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_email = {
                    executor.submit(
                        self.send_single_email, 
                        self.email_config["server"], self.email_config["port"], self.email_config["username_to"], self.email_config["username_from"], self.email_config["password"], i
                    ): i for i in range(current_mail)
                }

                for future in as_completed(future_to_email):
                    try:
                        success, email_num = future.result()
                        if success:
                            successful_count += 1
                        else:
                            failed_count += 1
                    except Exception as e:
                        failed_count += 1

            end_time = time.time()
            execution_time = end_time - start_time

            success_rate = (successful_count / current_mail) * 100 if current_mail > 0 else 0
            emails_per_second = successful_count / execution_time if execution_time > 0 else 0

            step_result = {
                'current_mail': current_mail,
                'successful': successful_count,
                'failed': failed_count,
                'success_rate': success_rate,
                'execution_time': execution_time,
                'emails_per_second': emails_per_second
            }
            results.append(step_result)
