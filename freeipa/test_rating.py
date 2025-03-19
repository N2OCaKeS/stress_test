from libs.libtable import Report
import numpy as np


report = Report()

# print(report.proc_errors, np.log1p(report.proc_errors))
rating_sr_znach = report.get_rating(report.user_count, report.sr_znach, y_min_for_mathmodel=0, y_max_for_mathmodel=1000)
rating_proc_errors = report.get_rating(report.user_count, report.proc_errors, y_min_for_mathmodel=0, y_max_for_mathmodel=100)
rating_last_values = report.get_rating(report.user_count, report.value_for_last_proc_delay, y_min_for_mathmodel=0, y_max_for_mathmodel=1000)
total_rating = report.get_total_rating([rating_sr_znach, rating_proc_errors, rating_last_values], [0.15, 0.7, 0.15])

print("Рейтинг среднее время аутентификации:   ", rating_sr_znach, rating_sr_znach * 0.15)
print("Рейтинг % ошибок: \t\t\t", rating_proc_errors, rating_proc_errors * 0.7)
print("Рейтинг время последней аутентификации: ", rating_last_values, rating_last_values * 0.15)
print("-----\nИтоговый рейтинг: ", total_rating)

print("******")

# report2 = Report(file_name="ipa_report_fail.txt")
