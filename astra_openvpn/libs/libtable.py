import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import warnings
import re
import os
from libs.ovpnlib import astra_version
from ovpn_conf import REPORT_PATH, TEMPLATE_PATH, RANGE, VMS_COUNT, VMS
from datetime import datetime
from allta import SystemCommands

sys_cls = SystemCommands()


class Report:
    def __init__(self, report_path=REPORT_PATH, template_path=TEMPLATE_PATH, ranger=RANGE, vm_count=VMS_COUNT, vms=VMS):
        self.report_path = report_path
        self.template_path = template_path
        self.processed_dir = f"{self.report_path}/processed"
        self.ovpn_log = f"{self.report_path}/raw/openvpn/openvpn.log"
        self.raw_records = []
        self.session_records = []
        self.range = ranger
        self.vm_count = vm_count
        self.vms = vms
        self.criteria = []

    @classmethod
    def _set_format(cls):
        version = astra_version()
        if ".".join(version[0].split(".")[:2]) == "1.8":
            return ("%Y-%m-%d %H:%M:%S", 2)
        elif ".".join(version[0].split(".")[:2]) == "1.7":
            return ("%a %b %d %H:%M:%S %Y", 5)
        else:
            return ("%a %b %d %H:%M:%S %Y", 5)


    def build(self):
        sys_cls.cmd(f"sudo mkdir -p {self.processed_dir}")

        log_format, time_parts = self._set_format()

        with open(self.ovpn_log, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                    
                parts = line.split()
                if len(parts) < time_parts + 1:  # Должно быть время + хотя бы 1 слово сообщения
                    continue
                    
                timestamp_str = ' '.join(parts[:time_parts])
                message = ' '.join(parts[time_parts:])
                
                try:
                    timestamp = datetime.strptime(timestamp_str, log_format)
                    self.raw_records.append({"timestamp": timestamp, "message": message})
                except ValueError as e:
                    print(f"Ошибка парсинга: {line.strip()} → {e}")
                    continue

        if not self.raw_records:
            print("е найдено ни одной записи в логе!")
            return

        df = pd.DataFrame(self.raw_records)
        
        if 'message' not in df.columns:
            print("В DataFrame отсутствует столбец 'message'")
            return

        start_sessions = df[df["message"].str.contains("Peer Connection Initiated", na=False)].copy()

        for _, row in start_sessions.iterrows():
            try:
                ts = row["timestamp"]
                msg = row["message"]

                # IP и порт
                ip_port_match = re.search(r"\[AF_INET\](\d+\.\d+\.\d+\.\d+):(\d+)", msg)
                if not ip_port_match:
                    continue
                    
                ip, port = ip_port_match.groups()

                # CN (с проверкой на None)
                cn_match = re.search(r"\[(.+?)\]", msg)
                cn = cn_match.group(1) if cn_match else "UNKNOWN"

                # Все строки этой сессии
                pattern = re.escape(f"{ip}:{port}")
                sess_df = df[df["message"].str.contains(pattern, na=False)].copy()

                # Дополнительные данные (с проверкой на пустоту)
                assigned_ip = self._extract_value(sess_df, "primary virtual IP", r"(\d+\.\d+\.\d+\.\d+)")
                cipher = self._extract_value(sess_df, "Outgoing Data Channel: Cipher", r"Cipher '(.+?)'")
                tls_info = self._extract_tls_info(sess_df)
                config_file = self._extract_value(sess_df, "OPTIONS IMPORT", r"from:\s+(.+)")

                self.session_records.append({
                    "timestamp": ts,
                    "peer_ip": ip,
                    "peer_port": port,
                    "common_name": cn,
                    "assigned_ip": assigned_ip,
                    "cipher": cipher,
                    "tls_version": tls_info.get("version"),
                    "tls_cipher": tls_info.get("cipher"),
                    "config_file": config_file
                })
            except Exception as e:
                print(f"Ошибка обработки сессии: {e}")
                continue

        # Сохранение результатов
        self._save_results()
        self._generate_html_report()


    def _extract_value(self, df, pattern, regex):
        """Вспомогательный метод для извлечения данных"""
        rows = df[df["message"].str.contains(pattern, na=False)]
        if not rows.empty:
            match = re.search(regex, rows.iloc[0]["message"])
            return match.group(1) if match else None
        return None

    def _extract_tls_info(self, df):
        """Извлечение информации о TLS"""
        rows = df[df["message"].str.contains("Control Channel: TLS", na=False)]
        if not rows.empty:
            msg = rows.iloc[0]["message"]
            parts = [p.strip() for p in msg.split(",")]
            return {
                "version": parts[0].split()[-1],
                "cipher": parts[1].split()[-1] if len(parts) > 1 else None
            }
        return {"version": None, "cipher": None}

    def _save_results(self):
        """Сохранение результатов в файлы"""
        if not self.session_records:
            print("Нет данных для сохранения!")
            return

        sessions_df = pd.DataFrame(self.session_records)
        sessions_df_sorted = sessions_df.sort_values(by="common_name")
        sessions_unique = sessions_df_sorted.drop_duplicates(subset=["common_name"])

        counts_df = sessions_df.groupby("common_name").size().reset_index(name="session_count")
        counts_df_sorted = counts_df.sort_values(by="common_name")

        total_sessions = counts_df["session_count"].sum()
        summary_row = pd.DataFrame({
            "common_name": ["TOTAL_SESSIONS"],
            "session_count": [total_sessions]
        })
        counts_df_final = pd.concat([counts_df_sorted, summary_row], ignore_index=True)

        sessions_unique.to_csv(f"{self.processed_dir}/unique_clients.csv", index=False)
        counts_df_final.to_csv(f"{self.processed_dir}/sessions_count.csv", index=False)

        print(f"Уникальные клиенты: {self.processed_dir}/unique_clients.csv")
        print(f"Статистика сессий: {self.processed_dir}/sessions_count.csv")


    def _generate_html_report(self):
        counts_df = pd.read_csv(f"{self.processed_dir}/sessions_count.csv")
        
        # Показатели
        expected = self.range
        actual = len(counts_df) - 1
        failed = expected - actual
        failed_percents = f"{round(failed / (expected / 100), 2)}%"
        avg_reconnects = counts_df[counts_df['common_name'] != 'TOTAL_SESSIONS']['session_count'].mean()
        
        # Проверяем критерий (не более 1% ошибок)
        max_allowed_failed = expected * 0.01
        result = "PASS" if failed <= max_allowed_failed else "FAIL"
        
        # df для отчета
        report_df = pd.DataFrame({
            'expected_clients': [expected],
            'unique_clients': [actual],
            'failed_connects': [failed_percents],
            'average_reconnects': [avg_reconnects],
            'result': [result]
        })
        
        html_output = f"""
        <html>
        <head>
            <title>OpenVPN Test Report</title>
        </head>
        <body>
            <table>
                <tr>
                    <th>Expected Clients</th>
                    <th>Unique Clients</th>
                    <th>Failed Connects</th>
                    <th>Average Reconnects</th>
                    <th>Result</th>
                </tr>
                <tr>
                    <td>{expected}</td>
                    <td>{actual}</td>
                    <td>{failed_percents}</td>
                    <td>{avg_reconnects:.2f}</td>
                    <td class="{result.lower()}">{result}</td>
                </tr>
            </table>
            <p>Условие теста: Failed Connects должен быть &lt;= 1%</p>
        </body>
        </html>
        """
        
        # Сохраняем HTML файл
        with open(f"{self.template_path}/test_report.html", "w") as f:
            f.write(html_output)
        
        print(f"\nHTML создан: {self.template_path}/test_report.html")


    def pass_fail(self):
        df = pd.read_csv("./results/processed/sessions_count.csv")
        
        # Рассчитываем метрики
        expected = self.range
        actual = len(df) - 1
        failed = expected - actual
        failed_percent = (failed / expected) * 100
        avg_reconnects = df[df['common_name'] != 'TOTAL_SESSIONS']['session_count'].mean()
        
        # Определяем результат
        result = "FAIL" if failed_percent > 1 else "PASS"
        
        # Создаем df отчет
        simple_report = pd.DataFrame({
            'expected_clients': [expected],
            'unique_clients': [actual],
            'failed_connects(%)': [f"{failed_percent:.2f}%"],
            'average_reconnects': [avg_reconnects],
            'result': [result]
        })
        
        simple_report.to_csv(f"{self.processed_dir}/test_report.csv", index=False)
        
        print(simple_report.to_string(index=False))
        print(f"\nФайл с результатами: {self.processed_dir}/test_report.csv")
        

        return result
    

    def plot_waves(self):
        files = ["testvm2_counts.csv",
                "testvm3_counts.csv",
                "testvm4_counts.csv",
                "testvm5_counts.csv"]
        dir = f"{self.report_path}/raw/active"
        
        # Создаем словарь для накопления суммарных значений
        total_clients = {}
        
        for file in files:
            fpath = os.path.join(dir, file)
            if os.path.exists(fpath):
                df = pd.read_csv(fpath, header=None, names=["Минута", "Клиенты"])
                # Суммируем значения для каждой минуты
                for minute, clients in zip(df["Минута"], df["Клиенты"]):
                    if minute in total_clients:
                        total_clients[minute] += clients
                    else:
                        total_clients[minute] = clients
        
        # Преобразуем словарь в DataFrame для удобства построения
        total_df = pd.DataFrame(list(total_clients.items()), columns=["Минута", "Клиенты"])
        total_df = total_df.sort_values("Минута")  # Сортируем по минутам
        
        plt.figure(figsize=(12, 6))
        plt.plot(total_df["Минута"], total_df["Клиенты"], marker='o', color='blue', label='Всего клиентов')
        
        plt.title("Общее количество активных клиентов по минутам")
        plt.xlabel("Минута")
        plt.ylabel("Клиенты")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()

        plt_path = f"{self.report_path}/processed/cgraph.png"
        plt.savefig(plt_path)
        return plt_path

