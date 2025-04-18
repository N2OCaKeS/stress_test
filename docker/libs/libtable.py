import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import warnings
import sys
from sklearn.preprocessing import MinMaxScaler

sys.path.append(os.path.join(os.getcwd(), '..'))
from docker_conf import REPORT_PATH, REPORT_VARIABLES, NORMALIZED_CONSTANTS


# using in report.py
class Report:
    '''
    Содержит ф-ции вычисления total rating по формулам и методам, 
    а также общий процент ошибок и конвертация csv в html.
    '''

    def __init__(self, report_path=REPORT_PATH, report_variables=REPORT_VARIABLES, constants=NORMALIZED_CONSTANTS):
        self.report_path = report_path
        self.report_variables = report_variables
        self.system_dirs = [d for d in os.listdir(report_path) if d.startswith("docker_web_")]
        self.integrals = []
        self.constants = constants


    @staticmethod
    def parse_locust_step_history(history_csv_path: str, users_per_step: int) -> pd.DataFrame:
        '''
        Парсит файл истории Locust (raw или filtered),
        автоматически фильтрует raw, оставляя только шаги 1–6,
        и возвращает агрегированные данные по шагам.

        Args:
            history_csv_path (str): путь к CSV от Locust (может быть raw или *_filtered.csv)
            users_per_step (int): количество пользователей в одном шаге

        Returns:
            pd.DataFrame: по шагам со средними RPS, ошибками и временем отклика
        '''
        # 1) Если это не filtered, то отфильтровываем «сырые» данные
        if not history_csv_path.endswith('_filtered.csv'):
            df_raw = pd.read_csv(history_csv_path)
            # оставляем только полные шаги, без 0-го
            df_filtered = df_raw[
                (df_raw['User Count'] > 0) &
                (df_raw['User Count'] % users_per_step == 0)
            ].copy()
            df_filtered['Step Index'] = (df_filtered['User Count'] // users_per_step).astype(int)

            # сохраняем «filtered» файл рядом с исходником
            base, ext = os.path.splitext(history_csv_path)
            filtered_path = f"{base}_filtered{ext}"
            df_filtered.to_csv(filtered_path, index=False)

            df = df_filtered
        else:
            df = pd.read_csv(history_csv_path)

        # 2) Проверка обязательных колонок
        required_columns = ['User Count', 'Requests/s', 'Failures/s', 'Total Average Response Time']
        if not all(col in df.columns for col in required_columns):
            raise ValueError(f"Файл {history_csv_path} не содержит необходимые колонки: {required_columns}")

        # 3) Пересоздаём Step Index и оставляем только 1–6
        df['Step Index'] = (df['User Count'] // users_per_step).astype(int)
        df = df[(df['Step Index'] >= 1) & (df['Step Index'] <= 6)]

        # 4) Группируем и агрегируем по шагам
        result = df.groupby('Step Index', as_index=False).agg({
            'User Count': 'first',
            'Requests/s': 'mean',
            'Failures/s': 'mean',
            'Total Average Response Time': 'mean'
        })

        # 5) Переименовываем и считаем процент ошибок
        result.rename(columns={
            'Requests/s': 'Avg Requests/s',
            'Failures/s': 'Avg Failures/s',
            'Total Average Response Time': 'Avg Response Time'
        }, inplace=True)
        result['Failure Rate (%)'] = (result['Avg Failures/s'] / result['Avg Requests/s']).fillna(0) * 100

        return result


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
        Делаем аппроксимацию по нормазизованным данным критерия:

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
    def plot_approximation(df: pd.DataFrame, column: str, output_path: str, degree: int = 10):
        '''
        Возвращает значение ф-ции. 
        Отрисовывает графики аппроксимации.
        
        Args:
            df (pd.dataframe): входной датафрейм
            column (str): колонка критерия
            output_path (str): путь для сохранения графика
            degree (str): фактор полинома, по умолчанию 10

        Returns:
            f: ф-ция аппроксимации
        '''

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



    def failures_wrapper(self):
        '''
        Читает csv с общими и результатами и считает сколько % ошибок в тесте.

        Returns:
            error_percentages: процент ошибок
        '''

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

        Args:
            df_result: итоговый дф по шагам

        Return:
            int: умножение (1, 0.9, 0.66)
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
        '''
        Конвертирует csv в html.

        Args:
            csv_file: csv файл
            output_dir: путь сохранения
        '''
        
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

    
