import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import warnings
from sklearn.preprocessing import MinMaxScaler
from scipy import integrate


def normalize_dataframe(df: pd.DataFrame, columns: list) -> pd.DataFrame:
    df_norm = df.copy()
    for col in columns:
        scaler = MinMaxScaler()
        df_norm[[col]] = scaler.fit_transform(df[[col]])
    return df_norm


def data_aproximation(x, y, polinom_factor):
    while polinom_factor > 0:
        with warnings.catch_warnings():
            warnings.filterwarnings('error')
            try:
                return np.poly1d(np.polyfit(np.array(x), np.array(y), polinom_factor))
            except np.RankWarning:
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


def plot_approximation(df: pd.DataFrame, column: str, output_path: str, degree: int = 10):
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


if __name__ == "__main__":
    base_path = os.path.abspath(os.path.dirname(__file__))
    results_path = os.path.join(base_path, "results")

    system_dirs = [d for d in os.listdir(results_path) if d.startswith("docker_web_")]
    integrals = []

    for sys_dir in system_dirs:
        for variant in ["nginx_docker", "nginx_server"]:
            variant_path = os.path.join(results_path, sys_dir, variant)
            hist_path = os.path.join(variant_path, "results_stats_history.csv")

            if os.path.exists(hist_path):
                df_result = parse_locust_step_history(hist_path)

                output_csv = os.path.join(variant_path, "step_stats_summary.csv")
                df_result.to_csv(output_csv, index=False)

                # Изменено: разделил обработку - сначала считаем интегралы, потом нормализуем для графиков
                
                # 1. Считаем интегралы на ненормализованных данных
                f_rps = plot_approximation(df_result, "Avg Requests/s", os.path.join(variant_path, "rps_plot.png"))  # Новый код: график ненормализованных данных
                f_failures = plot_approximation(df_result, "Avg Failures/s", os.path.join(variant_path, "failures_plot.png"))
                f_response_time = plot_approximation(df_result, "Avg Response Time", os.path.join(variant_path, "response_time_plot.png"))

                min_users = df_result["User Count"].min()
                max_users = df_result["User Count"].max()

                integral_rps, _ = integrate.quad(f_rps, min_users, max_users)
                integral_failures, _ = integrate.quad(f_failures, min_users, max_users)
                integral_response_time, _ = integrate.quad(f_response_time, min_users, max_users)

                # 2. Теперь нормализуем данные только для визуализации
                columns_to_normalize = ["Avg Requests/s", "Avg Failures/s", "Avg Response Time"]
                df_normalized = normalize_dataframe(df_result, columns_to_normalize)
                
                # Новый код: сохраняем нормализованные данные
                output_csv_norm = os.path.join(variant_path, "step_stats_summary_normalized.csv")
                df_normalized.to_csv(output_csv_norm, index=False)
                
                # Новый код: строим графики для нормализованных данных (дополнительно)
                plot_approximation(df_normalized, "Avg Requests/s", os.path.join(variant_path, "normalized_rps_plot.png"))
                plot_approximation(df_normalized, "Avg Failures/s", os.path.join(variant_path, "normalized_failures_plot.png"))
                plot_approximation(df_normalized, "Avg Response Time", os.path.join(variant_path, "normalized_response_time_plot.png"))

                integrals.append({
                    'variant': variant,
                    'integral_rps': integral_rps,
                    'integral_failures': integral_failures,
                    'integral_response_time': integral_response_time
                })
                
                # Изменено: используем реальные значения для расчета рейтинга
                adjusted_failures = 0.001 * integral_rps if integral_failures < 0.001 else integral_failures
                print("adjusted_failures =", adjusted_failures)
                rating = (integral_rps * 0.2) + (1 / integral_response_time * 0.2) + (1 / adjusted_failures * 0.8)

                print(f"\nРезультаты для {variant}:")
                print(f"  Диапазон пользователей: {min_users}-{max_users}")
                print(f"  Интеграл RPS (абсолютный): {integral_rps:.2f}")
                print(f"  Интеграл ошибок: {integral_failures:.2f}")
                print(f"  Интеграл времени ответа: {integral_response_time:.2f}")
                print(f"  Рейтинг: {rating}")
                print(f"  Файлы сохранены в: {variant_path}")
            else:
                print(f"\nФайл не найден: {hist_path}")