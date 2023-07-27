# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2023
# ;===========================================================

import re
import pandas
import tarfile
import warnings
import pysnooper
import numpy as np
from time import time
from scipy import integrate
from os import listdir, chdir
from sklearn import preprocessing
from matplotlib import pyplot as plt
from pretty_html_table import build_table
from lsb_conf import SCRIPT_DIR, REPORT_DIR, LOG_DIR, \
    REPORT_FILENAME, RATING_FILENAME, \
    REGEXP_PARSERS, TEST_MEASURE, TEST_NAMES
from libs.liblsb import astra_version, astra_kernel_version


class Report:
    @pysnooper.snoop()
    def __init__(self,
                 ox_lo_lim,
                 ox_up_lim,
                 ox_step,
                 report_dir=REPORT_DIR):

        self.__report_dir = report_dir

        self.__ox_lower_limit = ox_lo_lim
        self.__ox_upper_limit = ox_up_lim
        self.__ox_step = ox_step
        self.__grid_factor = 2

        # graph size
        self.__width = 27
        self.__height = 15

        # читаем из файла
        with open("{}/{}".format(self.__report_dir, REPORT_FILENAME), 'r') as file:
            text = file.read()

            # объявляем новый дикт
            self.__raw_dict = {}
            for test, regexp in REGEXP_PARSERS.items():  # ищем совпаденяи по тестам

                # чтоб не искать по десять раз, объявлю
                result_tuples = re.findall(regexp, text)
                # объявляем новый дикт с кючами значениями
                self.__raw_dict[test] = {}
                self.__raw_dict[test]['parallel_threads'] = [float(self.__ox_upper_limit)]
                print('parallel_threads', [float(self.__ox_upper_limit)])
                self.__raw_dict[test]['value'] = [float(result_tuple[0]) for result_tuple in result_tuples]
                print('value', [float(result_tuple[0]) for result_tuple in result_tuples])
                self.__raw_dict[test]['time'] = [float(result_tuple[1]) for result_tuple in result_tuples]
                print('time', [float(result_tuple[1]) for result_tuple in result_tuples])
                self.__raw_dict[test]['samples'] = [float(result_tuple[2]) for result_tuple in result_tuples]
                print('samples', [float(result_tuple[2]) for result_tuple in result_tuples])

        # соберем датафреймы
        self.__raw_tables = {}
        for test in TEST_NAMES:
            self.__raw_tables[test] = pandas.DataFrame(self.__raw_dict[test])

    @staticmethod
    def cm_to_inch(value):
        return value / 2.54

    @staticmethod
    def data_aproximation(x, y, polinom_factor=10):
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
                except np.RankWarning:
                    polinom_factor -= 1

    @staticmethod
    def create_tar():
        '''
        Создать архив
        :return:
        '''
        time_mark = time()
        with tarfile.open('lsb_{v}_{m}_{k}_{t}.tar'.format(v=astra_version()[0],
                                                           m=astra_version()[1],
                                                           k=astra_kernel_version(),
                                                           t=time_mark), 'w') as tar:
            chdir(SCRIPT_DIR)
            for file in listdir(REPORT_DIR):
                tar.add('{}/{}'.format('report', file))

            for file in listdir(LOG_DIR):
                tar.add('{}/{}'.format('log', file))

    def create_beauty_table(self,
                            test,
                            table_name='lsb_{t}_table.html'):
        '''
        :param test: имя теста
        :param path: директория с файлами отчета
        :param table_name: имя файла html для сохранения таблицы
        :return:
        '''
        beauty_table = build_table(self.__raw_tables[test], 'blue_light')
        with open('{}/{}'.format(self.__report_dir, table_name.format(t=test)), 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)

    def create_beauty_tables(self,
                             test_list=TEST_NAMES,
                             table_name='lsb_{t}_table.html'):
        '''
        :param test: список имен тестов
        :param path: директория с файлами отчета
        :param table_name: имя файла html для сохранения таблицы
        :return:
        '''
        for test in test_list:
            self.create_beauty_table(test, table_name)

    def template_aproximated_graph(self,
                                   test,
                                   ox_param_table_name,
                                   oy_param_table_name,
                                   measures=TEST_MEASURE):
        '''
            Шаблон графика с апроксимацией и точками
        '''
        # points
        x = self.__raw_tables[test].loc[:, [ox_param_table_name]]
        y = self.__raw_tables[test].loc[:, [oy_param_table_name]]

        ox_lst = self.__raw_dict[test][ox_param_table_name]
        oy_lst = self.__raw_dict[test][oy_param_table_name]

        # построить f(x)
        aprx_x = np.arange(self.__ox_lower_limit, self.__ox_upper_limit-self.__ox_step, 0.1)
        aprx_f = self.data_aproximation(ox_lst, oy_lst)

        # построить график
        plt.figure(figsize=(self.cm_to_inch(self.__width), self.cm_to_inch(self.__height)))
        plt.plot(x, y, 'o'),
        plt.plot(aprx_x, aprx_f(aprx_x))
        plt.title('{digit_varsion}({mode}). {ytitle} benchmark value/{xtitle}'.format(digit_varsion=astra_version()[0],
                                                                                      mode=astra_version()[1],
                                                                                      xtitle=ox_param_table_name,
                                                                                      ytitle=test))
        # оформить график
        plt.xlabel(ox_param_table_name)
        ox_ticks = np.arange(self.__ox_lower_limit,
                             self.__ox_upper_limit,
                             self.__grid_factor)
        plt.xticks(ox_ticks, ox_ticks, rotation='vertical')
        plt.ylabel('{} bench value ({})'.format(test, measures[test]))
        plt.grid(True)

        # создать график в png
        plt.savefig('{p}/lsb_{t}_{ox}_bench_value_graph'.format(p=self.__report_dir,
                                                                t=test,
                                                                ox=ox_param_table_name))

    def create_dhry2reg_graph(self):
        self.template_aproximated_graph(test='dhry2reg',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_whetstone_double_graph(self):
        self.template_aproximated_graph(test='whetstone-double',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_execl_graph(self):
        self.template_aproximated_graph(test='execl',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_fstime_graph(self):
        self.template_aproximated_graph(test='fstime',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_fsbuffer_graph(self):
        self.template_aproximated_graph(test='fsbuffer',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_fsdisk_graph(self):
        self.template_aproximated_graph(test='fsdisk',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_pipe_graph(self):
        self.template_aproximated_graph(test='pipe',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_context1_graph(self):
        self.template_aproximated_graph(test='context1',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_spawn_graph(self):
        self.template_aproximated_graph(test='spawn',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_shell1_graph(self):
        self.template_aproximated_graph(test='shell1',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_shell8_graph(self):
        self.template_aproximated_graph(test='shell8',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_syscall_graph(self):
        self.template_aproximated_graph(test='syscall',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_all_graphs(self):
        for test_name in TEST_NAMES:
            self.template_aproximated_graph(test=test_name,
                                            ox_param_table_name='parallel_threads',
                                            oy_param_table_name='value',
                                            measures=TEST_MEASURE)

    def get_rating(self,
                   test,
                   accuracy=3,
                   multiplier=10**(2),
                   auto_normalize=True):
        '''
        :param test: имя теста
        :param accuracy: точность, получаемых значений
        :param multiplier: множитель
        :param auto_normalize: нормализация 0-1
        :return: рейтинг
        '''
        if auto_normalize:
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(self.__raw_dict[test]['value'])[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array)]

            func_speed = self.data_aproximation(self.__raw_dict[test]['parallel_threads'], normalized_data_list)
            i_spd, err = integrate.quad(func_speed,
                                        self.__ox_lower_limit,
                                        self.__ox_upper_limit-self.__ox_step)
            if i_spd == 0:
                return 1
            else:
                return round((i_spd * multiplier), accuracy)
        else:
            pass

    def get_dhry2reg_rating(self):
        return self.get_rating('dhry2reg')

    def get_whetstone_double_rating(self):
        return self.get_rating('whetstone-double')

    def get_execl_rating(self):
        return self.get_rating('execl')

    def get_fstime_rating(self):
        return self.get_rating('fstime')

    def get_fsbuffer_rating(self):
        return self.get_rating('fsbuffer')

    def get_fsdisk_rating(self):
        return self.get_rating('fsdisk')

    def get_pipe_rating(self):
        return self.get_rating('pipe')

    def get_context1_rating(self):
        return self.get_rating('context1')

    def get_spawn_rating(self):
        return self.get_rating('spawn')

    def get_shell1_rating(self):
        return self.get_rating('shell1')

    def get_shell8_rating(self):
        return self.get_rating('shell8')

    def get_syscall_rating(self):
        return self.get_rating('syscall')

    def get_all_ratings(self):
        ratings = {}
        rating_file = '{}/{}'.format(self.__report_dir, RATING_FILENAME)

        # очистить отчет
        f = open(rating_file, 'w')
        f.close()

        # # вычисляем рейтинги и складываем в лист и в файл
        # for test_name in TEST_NAMES:
        #     rating = self.get_rating(test_name)
        #     ratings[test_name] = rating
        #     with open(rating_file, 'a+') as target_file:
        #         target_file.write('{}: {}\n'.format(test_name, rating))

        with open(REPORT_FILENAME, 'r') as report_file:
            for line in report_file:
                if "" in line:
                    total_rating = line.strip("\n").split(" ")[1]
                    ratings['total_rating'] = total_rating
                    with open(rating_file, "w") as file_for_rating:
                        file_for_rating.write(total_rating)

        return ratings



