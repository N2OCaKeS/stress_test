from scipy import integrate
from scipy.integrate import IntegrationWarning
import os
import warnings
from libs.libtable import Report
from libs.docker_conf import TEMPLATE_PATH
from garbidge import funcsnargs as fcs


rp = Report()
errors = rp.failures_wrapper()
print(rp.system_dirs)

for sys_dir in rp.system_dirs:
    for variant in ["nginx_docker", "nginx_server"]:
        variant_path = os.path.join(rp.report_path, sys_dir, variant)
        hist_path = os.path.join(variant_path, "results_stats_history.csv")
        if os.path.exists(hist_path):
            print(f"\nОбработка: {sys_dir}_{variant}")

            # Создаем дашборд производительности
            dashboard_path = os.path.join(variant_path, "performance_dashboard.png")
            # create_performance_dashboard(hist_path, dashboard_path)

            # Парсим и анализируем данные
            df_result = rp.parse_locust_step_history(hist_path)
            output_csv = os.path.join(variant_path, "step_stats_summary.csv")
            df_result.to_csv(output_csv, index=False)
            print(f"Сохранена статистика по шагам: {output_csv}")
            # Нормализация данных
            columns_to_normalize = ["Avg Requests/s", "Avg Failures/s", "Avg Response Time"]
            df_normalized = rp.normalize_dataframe(df_result, columns_to_normalize)
            output_csv_norm = os.path.join(variant_path, "step_stats_summary_normalized.csv")
            df_normalized.to_csv(output_csv_norm, index=False)
            # Аппроксимация и визуализация
            f_rps = rp.plot_approximation(df_normalized, "Avg Requests/s", 
                                     os.path.join(variant_path, "normalized_rps_plot.png"))
            f_failures = rp.plot_approximation(df_normalized, "Avg Failures/s", 
                                          os.path.join(variant_path, "normalized_failures_plot.png"))
            f_response_time = rp.plot_approximation(df_normalized, "Avg Response Time", 
                                               os.path.join(variant_path, "normalized_response_time_plot.png"))
            # Расчет интегралов
            min_users = df_normalized["User Count"].min()
            max_users = df_normalized["User Count"].max()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", IntegrationWarning)
                integral_rps, _ = integrate.quad(f_rps, min_users, max_users)
                integral_response_time, _ = integrate.quad(f_response_time, min_users, max_users)
            rp.integrals.append({
                'variant': f"{sys_dir}_{variant}",
                'integral_rps': integral_rps,
                'integral_response_time': integral_response_time
            })
            # Расчет рейтинга
            rating_base = (integral_rps * 0.33) + (1 / (integral_response_time * 0.33))
            step_multiplier, error_level = rp.get_step_error_multiplier(df_result)
            rating = rating_base * step_multiplier

            key = f"{sys_dir}_{variant}"
            rating_path = os.path.join(variant_path, "rating.txt")
            with open(rating_path, 'w') as file:
                file.write(f"{key}:\n")
                file.write(f"Рейтинг = {rating:.2f} (исходный: {rating_base:.2f}, множитель: {step_multiplier})\n")
                file.write(f"{error_level}\n")

                if key in errors and errors[key] is not None:
                    value = errors[key]
                    if value == 0:
                        file.write(f"Общий процент ошибок: 0% — ошибок нет\n")
                    elif value <= 1:
                        file.write(f"Общий процент ошибок: {value:.2f}% — незначительные\n")
                    elif 1 < value < 5:
                        file.write(f"Общий процент ошибок: {value:.2f}% — низкие\n")
                    elif 5 < value < 10:
                        file.write(f"Общий процент ошибок: {value:.2f}% — средние\n")
                    else:
                        file.write(f"Общий процент ошибок: {value:.2f}% — высокие\n")
                else:
                    file.write("Данные об ошибках не найдены\n")

                file.write(f"\nФайлы результатов:\n")
                file.write(f"- {output_csv}\n")
                file.write(f"- {output_csv_norm}\n")
                file.write(f"- {dashboard_path}\n")
                file.write(f"\nМетрики:\n")
                file.write(f"Integral RPS: {integral_rps:.2f}\n")
                file.write(f"Integral Response Time: {integral_response_time:.2f}\n")
            print(f"Анализ завершен для {key}. Результаты в {variant_path}")
        else:
            print(f"{sys_dir}_{variant}: Файл истории не найден: {hist_path}")
print("\nВсе тесты обработаны. Итоговые интегралы:")

for item in rp.integrals:
    print(f"{item['variant']}: RPS={item['integral_rps']:.2f}, RT={item['integral_response_time']:.2f}")

print("\nКонвертация CSV в HTML:")
for sys_dir in rp.system_dirs:
    for variant in ["nginx_docker", "nginx_server"]:
        variant_path = os.path.join(rp.report_path, sys_dir, variant)
        if os.path.exists(variant_path):
            for file in os.listdir(variant_path):
                if file.endswith(".csv"):
                    csv_path = os.path.join(variant_path, file)

                    # определяем целевую поддиректорию
                    subfolder = "docker" if "docker" in variant else "server"

                    # путь куда сохранить html
                    output_dir = os.path.join(TEMPLATE_PATH, subfolder)
                    # передаем нужный путь для html
                    fcs.html_converter(csv_path, output_dir)

