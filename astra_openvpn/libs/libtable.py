import pandas as pd
import re
from ovpn_conf import REPORT_PATH
from datetime import datetime

class Report:
    def __init__(self, report_path=REPORT_PATH):
        self.report_path = report_path
        self.raw_records = []  
        self.session_records = []  

    def build(self):
        with open(f"{self.report_path}/raw_results/openvpn/openvpn.log", "r") as f:
            lines = f.readlines()
            for line in lines:
                timestamp_str = ' '.join(line.split()[:5])
                if len(timestamp_str.split()) != 5:
                    continue
                else:
                    try:
                        timestamp = datetime.strptime(timestamp_str, "%a %b %d %H:%M:%S %Y")
                        message = ' '.join(line.split()[5:])
                        self.raw_records.append({"timestamp": timestamp, "message": message})
                    except Exception as e:
                        print(f"Ошибка парсинга: {line.strip()} → {e}")

        # === Создание DataFrame из сырых логов ===
        simple_df = pd.DataFrame(self.raw_records)

        # === Поиск начала сессий ===
        start_session = simple_df[simple_df["message"].str.contains("TLS: Initial packet from")].copy()

        for idx, row in start_session.iterrows():
            session_ts = row["timestamp"]

            # IP:порт клиента
            match = re.search(r"(\d+\.\d+\.\d+\.\d+):(\d+)", row['message'])
            if not match:
                continue
            ip = match.group(1)
            port = match.group(2)

            # Все сообщения по этому IP:порту
            pattern = f"{ip}:{port}"
            session_df = simple_df[simple_df['message'].str.contains(pattern)].copy()

            # common_name
            cn_rows = session_df[session_df['message'].str.contains("VERIFY OK: depth=0, CN=")]
            cn = cn_rows['message'].str.extract(r"CN=(.+)")[0].iloc[0] if not cn_rows.empty else None

            # assigned_ip
            assigned_rows = session_df[session_df['message'].str.contains("primary virtual IP")]
            assigned_ip = re.search(r"(\d+\.\d+\.\d+\.\d+)", assigned_rows['message'].iloc[0]).group(1) if not assigned_rows.empty else None

            # cipher
            cipher_row = session_df[session_df['message'].str.contains("Outgoing Data Channel: Cipher")]
            cipher = re.search(r"Cipher '(.+?)'", cipher_row['message'].iloc[0]).group(1) if not cipher_row.empty else None

            # TLS version и шифр
            tls_row = session_df[session_df['message'].str.contains("Control Channel: TLS")]
            if not tls_row.empty:
                tls_msg = tls_row['message'].iloc[0]
                parts = tls_msg.split(",")
                tls_version = parts[0].split()[-1]
                tls_cipher = parts[1].strip().split()[-1]
            else:
                tls_version = tls_cipher = None

            # config_file
            config_row = session_df[session_df['message'].str.contains("OPTIONS IMPORT")]
            config_file = config_row['message'].str.extract(r"from:\s+(.+)")[0].iloc[0] if not config_row.empty else None

            # добавление итоговой записи о сессии
            self.session_records.append({
                "timestamp": session_ts,
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
        print(sessions_df.head())

        sessions_df.to_csv(f"{self.report_path}/processed_results.csv", index=False)

    
    def test(self):
        df = pd.read_csv(f"{self.report_path}/processed_results.csv")

        print(len(df), "Клиентов подключилось")