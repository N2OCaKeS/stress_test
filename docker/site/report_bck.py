import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import warnings
from sklearn.preprocessing import MinMaxScaler
from scipy import integrate
from scipy.integrate import IntegrationWarning

base_path = os.path.abspath(os.path.dirname(__file__))
results_path = os.path.join(base_path, "results")
system_dirs = [d for d in os.listdir(results_path) if d.startswith("docker_web_")]
integrals = []


def normalize_dataframe(df: pd.DataFrame, columns: list, factor: float = 10) -> pd.DataFrame:
    df_norm = df.copy()
    for col in columns:
        min_val = 0
        max_val = df_norm[col].max() * factor
        df_norm[col] = (df_norm[col] - min_val) / (max_val - min_val) # == MinMaxScaler()
    return df_norm

def data_aproximation(x, y, polinom_factor):
    while polinom_factor > 0:
        with warnings.catch_warnings():
            warnings.filterwarnings('error')
            try:
                return np.poly1d(np.polyfit(np.array(x), np.array(y), polinom_factor))
            except Warning:
                polinom_factor -= 1
    raise ValueError("Не удалось построить аппроксимацию даже 1-й степени")


def parse_locust_step_history(history_csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(history_csv_path)
    df_agg = df[(df["Name"] == "Aggregated") & (df["User Count"] > 0)].copy()

    df_selected = df_agg[[
        "User Count", "Requests/s", "Failures/s", "Total Average Response Time"
    ]].copy()

    df_selected.rename(columns={"Total Average Response Time": "Average Response Time"}, inplace=True)
    df_selected["Step Index"] = (df_selected["User Count"] != df_selected["User Count"].shift()).cumsum()

    result = df_selected.groupby("Step Index").agg({
        "User Count": "first",
        "Requests/s": "mean",
        "Failures/s": "mean",
        "Average Response Time": "mean"
    }).reset_index()

    result.columns = ["Step Index", "User Count", "Avg Requests/s", "Avg Failures/s", "Avg Response Time"]
    return result


def plot_approximation(df: pd.DataFrame, column: str, output_path: str, degree: int = 5):
    x = df["User Count"]
    y = df[column]

    f = data_aproximation(x, y, degree)

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


def failures_wrapper():
    error_percentages = {}
    for sys_dir in system_dirs:
        for variant in ["nginx_docker", "nginx_server"]:
            variant_path = os.path.join(results_path, sys_dir, variant)
            hist_path = os.path.join(variant_path, "results_stats.csv")

            if os.path.exists(hist_path):
                df = pd.read_csv(hist_path)

                # Исключаем строку "Aggregated"
                df = df[df["Name"].str.strip().str.lower() != "aggregated"]

                try:
                    total_requests = df["Request Count"].sum()
                    total_failures = df["Failure Count"].sum()
                except KeyError as e:
                    print(f"Ошибка: не найдены нужные колонки в {hist_path}: {e}")
                    continue

                if total_requests == 0:
                    error_percentage = 100.0
                else:
                    error_percentage = (total_failures / total_requests) * 100

                error_percentages[f"{sys_dir}_{variant}"] = error_percentage
                print(f"{sys_dir}_{variant}: {total_failures} ошибок из {total_requests} → {error_percentage:.2f}%")

    return error_percentages


if __name__ == "__main__":
    errors = failures_wrapper()

    for sys_dir in system_dirs:
        for variant in ["nginx_docker", "nginx_server"]:
            variant_path = os.path.join(results_path, sys_dir, variant)
            hist_path = os.path.join(variant_path, "results_stats_history.csv")

            if os.path.exists(hist_path):
                df_result = parse_locust_step_history(hist_path)

                output_csv = os.path.join(variant_path, "step_stats_summary.csv")
                df_result.to_csv(output_csv, index=False)

                columns_to_normalize = ["Avg Requests/s", "Avg Failures/s", "Avg Response Time"]
                df_normalized = normalize_dataframe(df_result, columns_to_normalize)

                output_csv_norm = os.path.join(variant_path, "step_stats_summary_normalized.csv")
                df_normalized.to_csv(output_csv_norm, index=False)

                f_rps = plot_approximation(df_normalized, "Avg Requests/s", os.path.join(variant_path, "normalized_rps_plot.png"))
                f_failures = plot_approximation(df_normalized, "Avg Failures/s", os.path.join(variant_path, "normalized_failures_plot.png"))
                f_response_time = plot_approximation(df_normalized, "Avg Response Time", os.path.join(variant_path, "normalized_response_time_plot.png"))

                min_users = df_normalized["User Count"].min()
                max_users = df_normalized["User Count"].max()

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", IntegrationWarning)
                    integral_rps, _ = integrate.quad(f_rps, min_users, max_users)
                    integral_response_time, _ = integrate.quad(f_response_time, min_users, max_users)

                integrals.append({
                    'variant': f"{sys_dir}_{variant}",
                    'integral_rps': integral_rps,
                    'integral_response_time': integral_response_time
                })

                rating = (integral_rps * 0.5) + (1 / integral_response_time * 0.5)

                key = f"{sys_dir}_{variant}"
                rating_path = os.path.join(variant_path, "rating") 

                with open(rating_path, 'w') as file:
                    if key in errors:
                        value = errors[key]
                        if value == 0:
                            file.write(f"{key}: Рейтинг = {rating:.2f} \n| Ошибки отсутствуют\n")
                        elif value <= 5:
                            file.write(f"{key}: Рейтинг = {rating / 100 * 91.25:.2f} \n| Ошибки низкие ({value:.2f}%)\n")
                        elif 5 < value < 10:
                            file.write(f"{key}: Рейтинг = {rating / 100 * 82.5:.2f} \n| Ошибки средние ({value:.2f}%)\n")
                        else:
                            file.write(f"{key}: Рейтинг = {rating / 100 * 66.6:.2f} \n| Ошибки высокие ({value:.2f}%)\n")
                    else:
                        file.write(f"{key}: Данные об ошибках не найдены\n")

                    file.write(f"| Сохранено: {output_csv}\n")
                    file.write(f"| Интегралы: {integrals[-1]}\n\n")

            else:
                print(f"{sys_dir}_{variant}: Не найден файл истории: {hist_path}")
