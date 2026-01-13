import smtplib
import threading
import time
import socket
from email.mime.text import MIMEText
from exb_conf import MAIL_START, MAIL_MAX, MAIL_STEP, MAX_WORKERS, REPORT_FILENAME
from concurrent.futures import ThreadPoolExecutor, as_completed

from libs.libtable import Report


class SMTPTest:
    
    def __init__(self):
        self.email_config = {
            "server": 'localhost',
            "port": '25',
            "username_to": 'user2@stress-testing.local',
            "username_from": 'user1@stress-testing.local',
            "password": "1",
        }


    def send_single_email(self, i):
        try:
            with smtplib.SMTP(self.email_config["server"], self.email_config["port"], timeout=5) as smtp:
                # smtp.ehlo()
                # smtp.login(self.email_config["username_from"], self.email_config["password"])
                msg = MIMEText(f"Test email {i}")
                msg['Subject'] = f"Load Test {i}"
                msg['From'] = self.email_config["username_from"]
                msg['To'] = self.email_config["username_to"]
                errors = smtp.send_message(msg)
                if not errors:
                    return True, i, "success"
                else:
                    return False, i, "recipient_rejected"
                
        except smtplib.SMTPRecipientsRefused:
            return False, i, "user_not_found"
        except (smtplib.SMTPServerDisconnected, ConnectionRefusedError):
            return False, i, "server_down_or_busy"
        except (smtplib.SMTPConnectError, socket.timeout):
            return False, i, "timeout_or_conn_error"
        except smtplib.SMTPResponseException as e:
            return False, i, f"smtp_error_{e.smtp_code}"
        except Exception as e:
            return False, i, "other_error"


    def run_test(self):
            for current_mail in range(MAIL_START, MAIL_MAX + MAIL_STEP, MAIL_STEP):
                start_time = time.time()
                stats = {
                    "success": 0,
                    "user_not_found": 0,
                    "server_down_or_busy": 0,
                    "timeout_or_conn_error": 0,
                    "other_error": 0
                }
                
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    future_to_email = {
                        executor.submit(self.send_single_email, i): i for i in range(current_mail)
                    }

                    for future in as_completed(future_to_email):
                        try:
                            success, num, category = future.result()
                            if category not in stats:
                                stats[category] = 0
                            stats[category] += 1
                        except Exception:
                            stats["other_error"] += 1

                execution_time = time.time() - start_time
                emails_per_second = round(stats["success"] / execution_time if execution_time > 0 else 0, 2)

                with open(REPORT_FILENAME, 'a') as report_file:
                    report_file.write(f"{current_mail} {stats['success']} {emails_per_second}\n")

                print(f"\n--- Batch: {current_mail} emails ---")
                print(f"Time: {execution_time:.2f}s | Speed: {round(emails_per_second)} msg/sec")
                for cat, count in stats.items():
                    if count > 0:
                        print(f"  - {cat}: {count}")
                

if __name__ == "__main__":
    t = SMTPTest()
    t.run_test()
    r = Report()
    print(r.raw_table)