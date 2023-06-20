# -*- coding: utf-8 -*-

# ;===========================================================
# ; Author: ivelikanov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import os
import re
import gzip
import lzma
import shutil

from datetime import datetime

report_error_log = dict()
root_path = '/var/log'
all_files = []
blask_list_files = ["installer", "apt", "dpkg"]

def sort_by_datetime(time_start_test, line_with_error):
    """
        Сортировка по дате и времени
    """
    def find_date_in_line(line):
        """
            Функция ищет дату и время в начале строки.
            Если находит, возвращает объект datetime, иначе вернет None
        """
        line_temp = line.split(" ")
        
        # i  variant 1 | variant2   | variant3
        # -------------------------------------
        # 0 - Oct, Dec | 2022-10-17 | нет даты
        # 1 - 12       | 23:02:01   | нет даты
        # 2 - 15:14:13 | Уже сам лог| нет даты

        # Первый вариант для Oct 24 15:01:12
        try:
            if line_temp[1] == '':
                time_object1 = datetime.strptime("{} {} {}".format(line_temp[0], line_temp[2], line_temp[3]), "%b %d %H:%M:%S")
            else:
                time_object1 = datetime.strptime("{} {} {}".format(line_temp[0], line_temp[1], line_temp[2]), "%b %d %H:%M:%S")
        except:
            time_object1 = None

        # Второй вариант для 2022-10-17 23:02:01
        try:
            time_object_temp = datetime.strptime("{} {}".format(line_temp[0], line_temp[1]), "%Y-%m-%d %H:%M:%S").strftime("%m-%d %H:%M:%S")
            time_object2 = datetime.strptime(time_object_temp, "%m-%d %H:%M:%S")
        except:
            time_object2 = None

        if time_object1:
            return time_object1
        elif time_object2:
            return time_object2
        else:
            return None
    
    time_obj = find_date_in_line(line_with_error)
    if time_obj:
        if time_start_test < time_obj:
            return True
        else:
            return False
    else:
        return True


def find_errors_in_line(start_dt, line):
    """
        Поиск слова error построчно в логе. Если найдено, возвращает эту строку
    """
    if re.findall(r'[Ee][Rr][Rr][Oo][Rr]', line):
        if 'onnect! error message from' not in line:
            sort_date = sort_by_datetime(start_dt, line)
            if sort_date:    
                return line


def collecting_logs(path, time_start):
    """
        Вторым аргументом time_start функция должна принимать объект datetime
    """
    time_start_without_year =  datetime.strptime(time_start.strftime("%m-%d %H:%M:%S"), "%m-%d %H:%M:%S")
    """
        Поиск всех файлов в /var/log
    """
    for root, dirs, files in os.walk(root_path):
        for name in files:
            filename = os.path.join(root, name)
            all_files.append(filename)
    
    """
        Удаление из списка файлов которые заданы в black_list_files
    """
    for file in all_files[:]:
        for name in blask_list_files:
            if name in file:
                all_files.remove(file)

    """
        Чтение файла
    """
    for file in all_files:
        file_with_only_errors = ""
        if ".gz" in file:
            with gzip.open(file, 'rb') as file_input:
                with open(file[:-3], 'wb') as file_output:
                    shutil.copyfileobj(file_input, file_output)
            file = file[:-3]
        if ".xz" in file:
            with lzma.open(file) as log_file:
                for line in log_file:
                    line = line.decode('utf-8')
                    line_with_error = find_errors_in_line(time_start_without_year, line)
                    if line_with_error:
                        file_with_only_errors += line_with_error
        else:
            try:
                with open(file, 'r', encoding='UTF-8') as log_file:
                    for line in log_file:
                        line_with_error = find_errors_in_line(time_start_without_year, line)
                        if line_with_error:
                            file_with_only_errors += line_with_error 
            except UnicodeDecodeError:
                pass
        report_error_log['{}'.format(file)] = file_with_only_errors

    """
        Запись результатов в отчет
    """
    with open('{}/report_error.txt'.format(path), 'w') as report_file:
        for file, error_log in report_error_log.items():
            report_file.writelines('FILE_NAME: {file_name}\nLOGS:\n{report_text}\n----------------------------------------------------------\n'.format(file_name=file, 
                                                                                                                                                    report_text=error_log))