import imaplib
import smtplib
import threading
import time
import socket
import numpy as np

from email.mime.text import MIMEText
from concurrent.futures import ThreadPoolExecutor, as_completed

from libs.libtable import Report
from exb_conf import MAIL_START, MAIL_MAX, MAIL_STEP, MAX_WORKERS, REPORT_FILENAME, MAIL_USERS_QTY_START, MAIL_USERS_QTY_MAX, MAIL_USERS_QTY_STEP


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

# TODO
class IMAPTest:
    def __init__(self):
        self.config = {
            "server": 'localhost',
            "imap_port": '143',
            "smtp_port": '25',
            "username_from": 'user0@stress-testing.local',
        }

    def prepare_create_emails_for_all_users(self, users_qty):
        for user_idx in range(1, users_qty + 1):
            for email_idx in range(15):
                with smtplib.SMTP(self.config["server"], self.config["smtp_port"], timeout=30) as smtp:
                    msg = MIMEText(f"Test email {email_idx} for user{user_idx}")
                    msg['Subject'] = f"Load Test {email_idx} for user{user_idx}"
                    msg['From'] = self.config["username_from"]
                    msg['To'] = f"user{user_idx}@stress-testing.local"
                    errors = smtp.send_message(msg)

    def delete_emails_from_all_users(self, users_qty):
        for user_idx in range(1, users_qty + 1):
            imap = imaplib.IMAP4(self.config["server"], self.config["imap_port"])
            username = f"user{user_idx}"
            imap.login(username, "Testing_FOR_stress-testing_team13")
            imap.select("INBOX")
            status, data = imap.search(None, "ALL")
            mail_ids = data[0].split()
            for mail_id in mail_ids:
                imap.store(mail_id, '+FLAGS', '\\Deleted')
            imap.expunge()
            imap.logout()

    def imap_worker(self, idx):
        result = {"user": idx, "success": False, "latency": 0, "error": None}
        try:
            username = f"user{idx}"
            imap = imaplib.IMAP4(self.config["server"], self.config["imap_port"])
            imap.login(username, "Testing_FOR_stress-testing_team13")
            imap.select("INBOX")
            start_fetch = time.perf_counter()
            status, data = imap.fetch("1:*", "(UID FLAGS INTERNALDATE RFC822.SIZE ENVELOPE BODYSTRUCTURE)")
            fetch_time = (time.perf_counter() - start_fetch) * 1000  # in milliseconds
            imap.logout()

            result.update({
                    "success": status == "OK",
                    "latency": fetch_time
                })
        except Exception as e:
            result["error"] = str(e)

        return result

    def run_test(self):
        for users in range(MAIL_USERS_QTY_START, MAIL_USERS_QTY_MAX + MAIL_USERS_QTY_STEP, MAIL_USERS_QTY_STEP):
            self.prepare_create_emails_for_all_users(users_qty=users)

            batch_start = time.perf_counter()
            results = []
            
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(self.imap_worker, i) for i in range(1, users + 1)]
                for future in as_completed(futures):
                    res = future.result()
                    results.append(res)

            successful_latencies = [r["latency"] for r in results if r["success"]]
            print(successful_latencies)
            if successful_latencies:
                avg_latency = np.median(successful_latencies)
            else:
                avg_latency = -1

            batch_duration = time.perf_counter() - batch_start
            success_count = sum(1 for r in results if r["success"])
            error_count = users - success_count

            if batch_duration > 0:
                throughput = success_count / batch_duration
            else:
                throughput = 0.0

            with open(REPORT_FILENAME, 'a') as report_file:
                report_file.write(f"{users} {success_count} {error_count} {avg_latency:.4f} {throughput:.2f}\n")

            self.delete_emails_from_all_users(users_qty=users)


if __name__ == "__main__":
    # t = SMTPTest()
    # t.run_test()
    # r = Report()
    # print(r.raw_table)
    imp = IMAPTest()
    imp.run_test()
    # imp.prepare_create_emails_for_all_users(users_qty=10)
    # imp.delete_emails_from_all_users(users_qty=10)