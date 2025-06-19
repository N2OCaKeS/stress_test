import pandas as pd
import re
import os
from ovpn_conf import REPORT_PATH, RANGE, VM_COUNT, VMS
from datetime import datetime
from pathlib import Path
from collections import Counter


class Report:
    def __init__(self, report_path=REPORT_PATH, ranger=RANGE, vm_count=VM_COUNT, vms=VMS):
        self.report_path = report_path
        self.ovpn_status_log = Path(self.report_path) / "raw_results/openvpn/openvpn-status.log"
        self.ovpn_log = Path(self.report_path) / "raw_results/openvpn/openvpn.log"
        self.raw_records = []  
        self.session_records = []  
        self.range = ranger
        self.vm_count = vm_count
        self.vms = vms
        self.counter = Counter()
        self.results = []


    def build(self):
        with open(self.ovpn_log, "r") as f:
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

        sessions_df.to_csv(f"{self.report_path}/processed_results/clients_info.csv", index=False)

    
    def con(self):
        df = pd.read_csv(f"{self.report_path}/processed_results/clients_info.csv")

        return len(df)
    

    def discon(self):
        """
        Ищет строки с отключением клиентов
        Возвращает: count строк
        """
        pattern = re.compile(r'(tester\d+)/[\d\.]+:\d+ .*ping-restart')  # общий случай
        alt_pattern = re.compile(r'\[(tester\d+)\] Inactivity timeout')  # альтернатива
        with self.ovpn_log.open("r") as file:
            for line in file:
                match = pattern.search(line)
                if not match:
                    match = alt_pattern.search(line)
                if match:
                    client = match.group(1)
                    self.counter[client] += 1
        return len(self.counter)
    

    def bytes_counter(self): 
        df = pd.read_csv(
            self.ovpn_status_log,
            sep=",",
            skiprows=3,
            nrows=self.range,
            names=["Common Name", "Real Address", "Bytes Received", "Bytes Sent", "Connected Since"]
        )
        
        df["Bytes Received"] = pd.to_numeric(df["Bytes Received"], errors="coerce").fillna(0)
        df["Bytes Sent"] = pd.to_numeric(df["Bytes Sent"], errors="coerce").fillna(0)
    
        # Исключаем 3 туннеля с наибольшим входящим трафиком
        df_filtered = df.sort_values("Bytes Received", ascending=False).iloc[self.vm_count-1:]
    
        received = df_filtered["Bytes Received"].sum() / 1024
        sent = df_filtered["Bytes Sent"].sum() / 1024
    
        return [received, sent]
    

    def parse_iperf_log(self, filename):
        """
        Парсит один файл лога и извлекает нужные данные.
        Возвращает данные как словарь.
        """
        with open(filename, "r") as file:
            log_data = file.read()

        # Извлекаем скорость (bandwidth)
        bandwidths = re.findall(r"(\d+\.\d+) (M|G)bits/sec", log_data)
        if len(bandwidths) > 1:
            bandwidth_values = [float(b[0]) for b in bandwidths[1:]]  # Пропускаем первое значение
            bandwidth = round(sum(bandwidth_values) / len(bandwidth_values), 4)  # Среднее значение
        else:
            bandwidth = 0

        # Извлекаем количество потерянных пакетов
        packet_loss_match = re.search(r"(\d+(\.\d+)?)% packet loss", log_data)
        if packet_loss_match:
            packet_loss = float(packet_loss_match.group(1))
        else:
            packet_loss = 0

        interval_transfers = re.findall(r"\[\s*\d+\]\s+\d+\.\d+\s*-\s*\d+\.\d+\s+sec\s+([\d\.]+)\s+([KMG])Bytes", log_data)
        total_mbytes = 0
        for value, unit in interval_transfers:
            val = float(value)
            if unit == "K":
                val /= 1024
            elif unit == "G":
                val *= 1024
            total_mbytes += val
        total_mbytes = round(total_mbytes, 4)

        ip_match = re.search(r"Binding to local address (\d+\.\d+\.\d+\.\d+)", log_data)
        ip_address = ip_match.group(1) if ip_match else "unknown"


        return {
            "Bandwidth (Mbps)": bandwidth,
            "Transfered Mb": total_mbytes,
            "Packet Loss (%)": packet_loss,
            "ip": ip_address,
        }


    def parse_all_logs(self):
        """
        Парсит все логи из указанной директории и сохраняет результаты.
        """
        for item in range(1, self.range + 1):
            for vm in self.vms[1:]:
                log_file = os.path.join(f"{self.report_path}/raw_results", f"iperf_{vm}", f"tun{item}.log")
                if os.path.exists(log_file):
                    result = self.parse_iperf_log(log_file)
                    result["Tun"] = f"tun{item}"
                    result["VM"] = vm
                    self.results.append(result)
        df = pd.DataFrame(self.results)
        df.to_csv(f"{self.report_path}/processed_results/iperf_stat.csv")
        return df