import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import warnings
from sklearn.preprocessing import MinMaxScaler
from scipy import integrate
from scipy.integrate import IntegrationWarning
from matplotlib.gridspec import GridSpec
from libs.docker_conf import REPORT_PATH, REPORT_VARIABLES, NORMALIZED_CONSTANTS


# using in report.py
class Report:
    def __init__(self, report_path=REPORT_PATH, report_variables=REPORT_VARIABLES, constants=NORMALIZED_CONSTANTS):
        self.report_path = report_path
        self.report_variables = report_variables
        self.system_dirs = [d for d in os.listdir(report_path) if d.startswith("docker_web_")]
        self.integrals = []
        self.constants = constants


    def normalize_dataframe(self, df, columns):
        """
        Нормализует указанные столбцы DataFrame, добавляя граничные значения из словаря constants.
    
        Параметры:
            df: Исходный DataFrame
            columns: Список столбцов для нормализации
    
        Возвращает:
            DataFrame с нормализованными столбцами (без добавленных граничных значений)
        """
        df_norm = df.copy()
        scaler = MinMaxScaler()
    
        for col in columns:
            min_val, max_val = self.constants.get(col, [0, 1])  # берем из словаря, по умолчанию [0,1]
    
            temp_lst = [min_val] + df_norm[col].tolist() + [max_val]
    
            normalized_data = scaler.fit_transform(np.array(temp_lst).reshape(-1, 1))
            normalized_data_list = normalized_data[1:-1].flatten().tolist()
    
            df_norm[col] = normalized_data_list
    
        return df_norm


    @staticmethod
    def data_approximation(x, y, polinom_factor=10):
        '''
        :param x: [x1, x1, x3, ...] последовательность значений x
        :param y: [y1, y1, y3, ...] последовательность значений y
        :param polinom_factor: коэффициент полиномизации
        :return: f(x)
        '''
        while True:
            with warnings.catch_warnings():
                warnings.filterwarnings('error')
                try:
                    return np.poly1d(np.polyfit(np.array(x), np.array(y), polinom_factor))
                except Warning:
                    polinom_factor -= 1


    @staticmethod
    def plot_approximation(df: pd.DataFrame, column: str, output_path: str, degree: int = 5):
        x = df["User Count"]
        y = df[column]

        f = Report.data_approximation(x, y, degree)

        x_fit = np.linspace(x.min(), x.max(), 200)
        y_fit = f(x_fit)

        plt.figure(figsize=(8, 5))
        plt.scatter(x, y, color='red', label="Нормализованные данные")
        plt.plot(x_fit, y_fit, color='blue', label=f"Аппроксимация (степень {degree})")
        plt.xlabel("User Count")
        plt.ylabel(column)
        plt.title(f"Аппроксимация: {column}")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(output_path)
        plt.close()
        return f

    @staticmethod
    def parse_locust_step_history(history_csv_path: str) -> pd.DataFrame:
        df = pd.read_csv(history_csv_path)
        df_agg = df[(df["Name"] == "Aggregated") & (df["User Count"] > 0)].copy()

        df_selected = df_agg[[
            "User Count", "Requests/s", "Failures/s", "Total Average Response Time"
        ]].copy()

        df_selected["Step Index"] = (df_selected["User Count"] != df_selected["User Count"].shift()).cumsum()

        result = df_selected.groupby("Step Index").agg({
            "User Count": "first",
            "Requests/s": "mean",
            "Failures/s": "mean",
            "Total Average Response Time": "mean"
        }).reset_index()

        # Переименовываем после агрегации
        result.rename(columns={
            "Requests/s": "Avg Requests/s",
            "Failures/s": "Avg Failures/s",
            "Total Average Response Time": "Avg Response Time"
        }, inplace=True)

        # Добавляем колонку с процентом ошибок
        result["Failure Rate (%)"] = (
            result["Avg Failures/s"] / result["Avg Requests/s"]
        ).fillna(0) * 100

        return result


    def failures_wrapper(self):
        error_percentages = {}
        for sys_dir in self.system_dirs:
            for variant in self.report_variables:
                variant_path = os.path.join(self.report_path, sys_dir, variant)
                hist_path = os.path.join(variant_path, "results_stats.csv")

                if os.path.exists(hist_path):
                    df = pd.read_csv(hist_path)
                    if "Name" in df.columns:
                        aggregated_df = df[df["Name"].str.strip().str.lower() == "aggregated"]
                    else:
                        aggregated_df = pd.DataFrame()

                    row = aggregated_df.iloc[0] if not aggregated_df.empty else df.iloc[0]
                    try:
                        request_count = float(row["Request Count"])
                        failure_count = float(row["Failure Count"])
                        error_percentage = (failure_count / request_count) * 100
                        error_percentages[f"{sys_dir}_{variant}"] = error_percentage
                    except (ValueError, KeyError) as e:
                        print(f"Ошибка при чтении данных из {hist_path}: {e}")
                        error_percentages[f"{sys_dir}_{variant}"] = None

        return error_percentages

    @staticmethod
    def get_step_error_multiplier(df_result: pd.DataFrame) -> tuple[float, str]:
        """
        Возвращает множитель рейтинга и описание по шагам:
        - если шаг >=10% → 0.66
        - если >0 → 0.9
        - иначе → 1.0
        """
        if "Failure Rate (%)" not in df_result.columns:
            return 1.0, "Колонка 'Failure Rate (%)' не найдена"

        failure_rates = df_result["Failure Rate (%)"]

        if any(failure_rates >= 10):
            return 0.66, "Ошибки высокие (≥10% на одном из шагов)"
        elif any(failure_rates > 0):
            return 0.9, "Ошибки средние (0–10% на одном из шагов)"
        else:
            return 1.0, "Ошибки отсутствуют"


    def html_converter(self, csv_file, output_dir):
        import pandas as pd
        import os

        df = pd.read_csv(csv_file)

        # Безопасно удаляем строку Aggregated, если колонка 'Name' есть
        if "Name" in df.columns:
            df = df[df["Name"] != "Aggregated"]

        html_table = df.to_html(index=False)

        base_name = os.path.basename(csv_file).replace(".csv", ".html")
        html_path = os.path.join(output_dir, base_name)

        os.makedirs(output_dir, exist_ok=True)
        with open(html_path, 'w') as file:
            file.write(html_table)

        print(f"HTML создан: {html_path}")

    
