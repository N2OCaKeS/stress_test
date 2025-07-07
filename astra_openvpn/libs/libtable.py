import pandas as pd
import re
import os
from ovpn_conf import REPORT_PATH, RANGE, VMS_COUNT, VMS
from datetime import datetime
from pathlib import Path
from collections import Counter


class Report:
    def __init__(self, report_path=REPORT_PATH, ranger=RANGE, vm_count=VMS_COUNT, vms=VMS):
        self.report_path = report_path
        self.ovpn_status_log = Path(self.report_path) / "raw/openvpn/openvpn-status.log"
        self.ovpn_log = Path(self.report_path) / "raw/openvpn/openvpn.log"
        self.raw_records = []  
        self.session_records = []  
        self.range = ranger
        self.vm_count = vm_count
        self.vms = vms
        self.counter = Counter()
        self.criteria = []


    def build(self):
        # Читаем лог
        with open(self.ovpn_log, "r") as f:
            lines = f.readlines()
            for line in lines:
                parts = line.split()
                if len(parts) < 6:
                    continue
                timestamp_str = ' '.join(parts[:5])
                try:
                    timestamp = datetime.strptime(timestamp_str, "%a %b %d %H:%M:%S %Y")
                    message = ' '.join(parts[5:])
                    self.raw_records.append({"timestamp": timestamp, "message": message})
                except Exception as e:
                    print(f"Ошибка парсинга: {line.strip()} → {e}")

        # Создаём DataFrame
        df = pd.DataFrame(self.raw_records)

        # Ищем строки начала сессии
        start_sessions = df[df["message"].str.contains("Peer Connection Initiated")].copy()

        for _, row in start_sessions.iterrows():
            ts = row["timestamp"]

            # IP и порт
            ip_port_match = re.search(r"\[AF_INET\](\d+\.\d+\.\d+\.\d+):(\d+)", row["message"])
            if not ip_port_match:
                continue
            ip = ip_port_match.group(1)
            port = ip_port_match.group(2)

            # CN
            cn_match = re.search(r"\[(.+?)\]", row["message"])
            cn = cn_match.group(1) if cn_match else None

            # Все строки этой сессии
            pattern = f"{ip}:{port}"
            sess_df = df[df["message"].str.contains(pattern)].copy()

            # assigned_ip
            assigned = sess_df[sess_df["message"].str.contains("primary virtual IP")]
            if not assigned.empty:
                assigned_ip = re.search(r"(\d+\.\d+\.\d+\.\d+)", assigned['message'].iloc[0]).group(1)
            else:
                assigned_ip = None

            # cipher
            cipher_row = sess_df[sess_df["message"].str.contains("Outgoing Data Channel: Cipher")]
            if not cipher_row.empty:
                cipher = re.search(r"Cipher '(.+?)'", cipher_row['message'].iloc[0]).group(1)
            else:
                cipher = None

            # TLS
            tls_row = sess_df[sess_df["message"].str.contains("Control Channel: TLS")]
            if not tls_row.empty:
                tls_msg = tls_row['message'].iloc[0]
                parts = tls_msg.split(",")
                tls_version = parts[0].split()[-1]
                tls_cipher = parts[1].strip().split()[-1]
            else:
                tls_version = tls_cipher = None

            # config_file
            config_row = sess_df[sess_df["message"].str.contains("OPTIONS IMPORT")]
            if not config_row.empty:
                config_file = config_row['message'].str.extract(r"from:\s+(.+)")[0].iloc[0]
            else:
                config_file = None

            # Добавляем запись
            self.session_records.append({
                "timestamp": ts,
                "peer_ip": ip,
                "peer_port": port,
                "common_name": cn,
                "assigned_ip": assigned_ip,
                "cipher": cipher,
                "tls_version": tls_version,
                "tls_cipher": tls_cipher,
                "config_file": config_file
            })

        sessions_df = pd.DataFrame(self.session_records)

        # Сортируем по common_name
        sessions_df_sorted = sessions_df.sort_values(by="common_name")

        # Оставляем только уникальные common_name
        sessions_unique = sessions_df_sorted.drop_duplicates(subset=["common_name"])

        # Считаем количество сессий каждого клиента
        counts_df = sessions_df.groupby("common_name").size().reset_index(name="session_count")
        counts_df_sorted = counts_df.sort_values(by="common_name")

        # Добавляем итоговую строку
        total_sessions = counts_df["session_count"].sum()
        summary_row = pd.DataFrame({
            "common_name": ["TOTAL_SESSIONS"],
            "session_count": [total_sessions]
        })
        counts_df_final = pd.concat([counts_df_sorted, summary_row], ignore_index=True)

        # Создаём папку для вывода
        output_dir = Path(self.report_path) / "processed"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Сохраняем результаты
        sessions_unique.to_csv(output_dir / "clients_info_unique.csv", index=False)
        counts_df_final.to_csv(output_dir / "clients_sessions_count.csv", index=False)

        print("✅ Готово!")
        print(f" - Уникальные клиенты: {output_dir/'clients_info_unique.csv'}")
        print(f" - Статистика по сессиям: {output_dir/'clients_sessions_count.csv'}")


    def pass_fail(self):
        df = pd.read_csv("./results/processed/clients_sessions_count.csv")
        if len(df) >= self.range / 100 * 99 - 1:
            self.criteria.append(True)
        else: self.criteria.append(False)
        
        avg_reconnects = df["session_count"].mean()

        if avg_reconnects <= 2:
            self.criteria.append(True)
        else: self.criteria.append(False)
        print(f"Критерий Unique clients - {"PASS" if self.criteria[0] == True else 'Fail'}\n"
              f"Критерий AVG Reconects - {"PASS" if self.criteria[1] == True else 'Fail'}")
        if self.criteria[0] and self.criteria[1]:
            return "PASS"
        else: return "FAIL"
