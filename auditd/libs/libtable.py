# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import tarfile
import pandas
import warnings
import numpy as np

from time import time
from scipy import integrate
from os import listdir, chdir
from sklearn import preprocessing
from matplotlib import pyplot as plt
from libs.libaub import astra_version, astra_kernel_version
from pretty_html_table import build_table
from aub_conf import SCRIPT_DIR, \
    LOG_DIR, REPORT_DIR, REPORT, \
    DEFAULT_PS_LIFETIME, DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY, PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP


class Report:
    def __init__(self,
                 event_names,
                 latency_report=None,
                 losses_report=None):
        '''
        :param event_names: наименование события audit
        :param latency_report: файл с отчетом по тесту get_latency_stat_psaud
        :param losses_report: файл с отчетом по тесту get_losses_stat_psaud
        '''

        self.__event_names = event_names
        self.__events_per_second_lower_limit = int(PS_LOWER_LIMIT) / float(DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY)
        self.__events_per_second_step = int(PS_STEP) / float(DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY)
        self.__events_per_second_upper_limit = int(PS_UPPER_LIMIT) / float(DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY)

        # graph size
        self.width = 27
        self.height = 15
        self.linecolors = ['black',
                           'dimgrey',
                           'red',
                           'orange',
                           'yellow',
                           'greenyellow',
                           'green',
                           'turquoise',
                           'skyblue',
                           'blue',
                           'blueviolet',
                           'fuchsia',
                           'pink',
                           'teal',
                           'silver',
                           'darkturquoise',
                           'indigo'
                           ]

        if latency_report:
            '''Организовать датафрейм по результатам теста get_latency_stat_psaud'''
            with open(latency_report, 'r') as report_file:
                raw_data = report_file.read().split()

            self.__event_name_lst = [str(i) for i in raw_data[0::2]]
            self.__latency_lst = [float(i) for i in raw_data[1::2]]
            self.__raw_table = pandas.DataFrame({'audit_event': self.__event_name_lst,
                                                 'latency': self.__latency_lst})

            self.__latency_raw_tables = {}
            for event in self.__event_names:
                self.__latency_raw_tables[event] = self.__raw_table[self.__raw_table.audit_event == event]

        if losses_report:
            '''Организовать датафрейм по результатам теста get_losses_stat_psaud'''
            with open(losses_report, 'r') as report_file:
                raw_data = report_file.read().split()

            self.__event_name_lst = [str(i) for i in raw_data[0::6]]
            self.__events_per_second_lst = [int(i) for i in raw_data[1::6]]
            self.__expected_event_quantity_lst = [int(i) for i in raw_data[2::6]]
            self.__real_event_quantity_lst = [int(i) for i in raw_data[3::6]]
            self.__result_in_percent_lst = [float(i) for i in raw_data[4::6]]
            self.__raw_table = pandas.DataFrame({'audit_event': self.__event_name_lst,
                                                 'eps': self.__events_per_second_lst,
                                                 'exp_ev': self.__expected_event_quantity_lst,
                                                 'real_ev': self.__real_event_quantity_lst,
                                                 'completed': self.__result_in_percent_lst})

            self.__losses_raw_tables = {}
            for event in self.__event_names:
                self.__losses_raw_tables[event] = self.__raw_table[self.__raw_table.audit_event == event]

        if losses_report and latency_report:
            '''Организовать датафреймы по результатам тестов get_latency_stat_psaud, get_losses_stat_psaud'''
            self.__main_raw_tables = {}
            for event in self.__event_names:
                self.__main_raw_tables[event] = pandas.concat([self.__losses_raw_tables[event],
                                                               self.__latency_raw_tables[event]['latency']], axis=1)
                self.__main_raw_tables[event].reset_index()
                #print(self.__main_raw_tables[event])

    @staticmethod
    def _cm_to_inch(value):
        '''
        :param value: сантиметры
        :return: дюймы
        '''
        return value / 2.54

    @staticmethod
    def _last_passed(lst1, lst2):
        '''
        Найти последний пройденный тест
        :param lst1: первый список (>=)
        :param lst2: второй список
        :return: индекс
        '''
        for index in range(len(lst1)):
            if lst1[index] >= lst2[index]:
                return index

    @staticmethod
    def _data_aproximation(x, y, polinom_factor=10):
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

    def create_beauty_table(self,
                            type,
                            path=REPORT_DIR,
                            table_name='aub_{t}_{e}_table.html'):
        '''
        :param path: директория с файлами отчета
        :param table_name: имя файла html для сохранения таблицы
        :return:
        '''
        for event in self.__event_names:
            beauty_table = build_table(self.__main_raw_tables[event], 'blue_light')
            with open('{}/{}'.format(path, table_name.format(t=type, e=event)), 'w') as beauty_html_table:
                beauty_html_table.write(beauty_table)

    @staticmethod
    def create_tar():
        '''
        Создать архив
        :return:
        '''
        time_mark = time()
        with tarfile.open('aub_{v}_{m}_{k}_{t}.tar'.format(v=astra_version()[0],
                                                           m=astra_version()[1],
                                                           k=astra_kernel_version(),
                                                           t=time_mark), 'w') as tar:
            chdir(SCRIPT_DIR)
            for file in listdir(REPORT_DIR):
                tar.add('{}/{}'.format('report', file))

            for file in listdir(LOG_DIR):
                tar.add('{}/{}'.format('log', file))

    def _template_aproximated_graph(self,
                                    event=None,
                                    raw_table=None,
                                    ox_param_table_name=None,
                                    oy_param_table_name=None,
                                    ox_lower_limit=None,
                                    ox_upper_limit=None,
                                    type=None,
                                    path=None):
        '''
        :param event: наименование события audit
        :param raw_table: датафрейм
        :param ox_param_table_name: имя столбца в таблице (OX)
        :param oy_param_table_name: имя столбца в таблице (OY)
        :param ox_lower_limit: нижняя граница значений
        :param ox_upper_limit: верхняя граница значений
        :param path: путь для сохранения результата
        :return:
        '''
        if raw_table is None:
            raw_table = self.__main_raw_tables[event]
        elif event is None:
            event = raw_table['audit_event'].values.tolist()[0]

        # точки
        x = raw_table.loc[:, [ox_param_table_name]]
        y = raw_table.loc[:, [oy_param_table_name]]

        ox_lst = raw_table[ox_param_table_name].values.tolist()
        oy_lst = raw_table[oy_param_table_name].values.tolist()

        # построить аппроксимирующую f(x)
        aprx_x = np.arange(ox_lower_limit, ox_upper_limit, 0.1)
        aprx_f = self._data_aproximation(ox_lst, oy_lst)

        # build graph
        figure = plt.figure(figsize=(self._cm_to_inch(self.width), self._cm_to_inch(self.height)))
        plt.plot(x, y, 'o')

        if max(oy_lst) != min(oy_lst):
            plt.plot(aprx_x, aprx_f(aprx_x))
            pass

        if oy_param_table_name == 'completed':
            plt_title = '{digit_varsion}{mode}. {event} {ytitle}(%)/{xtitle}'
            plt.ylabel('completed(%)')

            plt.plot(ox_lst, [100] * len(ox_lst), color='g')
            plt.plot(ox_lst, [50] * len(ox_lst), color='r')
            plt.ylim(bottom=0, top=105)

            green_lst = [True if res <= 100.0 else False for res in oy_lst]
            yellow_lst = [True if (res < 100.0) and (res > 50.0) else False for res in oy_lst]
            red_lst = [True if res <= 50.0 else False for res in oy_lst]

            # colorized zone
            plt.fill_between(ox_lst[0:], oy_lst[0:], where=green_lst, facecolor='palegreen', interpolate=True, alpha=0.7)
            plt.fill_between(ox_lst[0:], oy_lst[0:], where=yellow_lst, facecolor='yellow', interpolate=True, alpha=0.7)
            plt.fill_between(ox_lst[0:], oy_lst[0:], where=red_lst, facecolor='red', interpolate=True, alpha=0.7)

        elif oy_param_table_name == 'latency':
            plt_title = '{digit_varsion}{mode}. {event} {ytitle}(sec)/{xtitle}'
            plt.ylabel('latency(sec)')
            plt.ylim(bottom=-0.1*DEFAULT_PS_LIFETIME, top=2*DEFAULT_PS_LIFETIME)

            green_lst = [True if res >= 0.0 else False for res in oy_lst]
            red_lst = [True if res <= 0.0 else False for res in oy_lst]

            # colorized zone
            plt.fill_between(ox_lst[0:], oy_lst[0:], where=green_lst, facecolor='palegreen', interpolate=True, alpha=0.7)
            plt.fill_between(ox_lst[0:], oy_lst[0:], where=red_lst, facecolor='red', interpolate=True, alpha=0.7)
        else:
            plt_title = '{digit_varsion}{mode}. {event} {ytitle}/{xtitle}'

        plt.title(plt_title.format(digit_varsion=astra_version()[0],
                                   event=event.upper(),
                                   mode=astra_version()[1],
                                   xtitle=ox_param_table_name,
                                   ytitle=oy_param_table_name))
        plt.xlabel(ox_param_table_name)
        plt.grid()

        plt.savefig('{p}/aub_{t}_{ev}_{ox}_{oy}_graph'.format(p=path,
                                                              t=type,
                                                              ev=event,
                                                              ox=ox_param_table_name,
                                                              oy=oy_param_table_name))

    def _template_comparative_aproximated_graph(self,
                                                ox_param_table_name=None,
                                                oy_param_table_name=None,
                                                type=None,
                                                path=None):

        ox_lst = []
        figure = plt.figure(figsize=(self._cm_to_inch(self.width), self._cm_to_inch(self.height)))

        for event in enumerate(self.__event_names):
            raw_table = self.__main_raw_tables[event[1]]

            # точки
            x = raw_table.loc[:, [ox_param_table_name]]
            y = raw_table.loc[:, [oy_param_table_name]]

            ox_lst = raw_table[ox_param_table_name].values.tolist()

            # build graph
            plt.plot(x, y, label=event[1], linewidth=2, color=self.linecolors[event[0]])

        if oy_param_table_name == 'completed':
            plt_title = '{digit_varsion}{mode}. All events {ytitle}/{xtitle}'
            plt.ylabel('completed(%)')
            plt.plot(ox_lst, [100] * len(ox_lst), color='g', linestyle='dotted', linewidth=5)
            plt.plot(ox_lst, [50] * len(ox_lst), color='r', linestyle='dotted', linewidth=5)
            plt.ylim(bottom=0, top=105)
        elif oy_param_table_name == 'latency':
            plt_title = '{digit_varsion}{mode}. All events {ytitle}/{xtitle}'
            plt.ylabel('latency(sec)')
            plt.ylim(bottom=-0.1 * DEFAULT_PS_LIFETIME, top=3 * DEFAULT_PS_LIFETIME)
        else:
            plt_title = '{digit_varsion}{mode}. All events {ytitle}/{xtitle}'

        plt.title(plt_title.format(digit_varsion=astra_version()[0],
                                   mode=astra_version()[1],
                                   xtitle=ox_param_table_name,
                                   ytitle=oy_param_table_name))

        plt.xlabel(ox_param_table_name)
        plt.grid(True)
        plt.legend(loc='upper left')

        plt.savefig('{p}/aub_{t}_total_{ox}_{oy}_graph'.format(p=path,
                                                               t=type,
                                                               ox=ox_param_table_name,
                                                               oy=oy_param_table_name))

    def create_aub_latency_eps_graph(self, type, path=REPORT_DIR):
        for event in self.__event_names:
            self._template_aproximated_graph(event=event,
                                             ox_param_table_name='eps',
                                             oy_param_table_name='latency',
                                             ox_lower_limit=self.__events_per_second_lower_limit,
                                             ox_upper_limit=self.__events_per_second_upper_limit,
                                             type=type,
                                             path=path)

    def create_total_latency_eps_graph(self, type, path=REPORT_DIR):
        self._template_comparative_aproximated_graph(ox_param_table_name='eps',
                                                     oy_param_table_name='latency',
                                                     type=type,
                                                     path=path)

    def create_aub_losses_eps_graph(self, type, path=REPORT_DIR):
        for event in self.__event_names:
            self._template_aproximated_graph(event=event,
                                             ox_param_table_name='eps',
                                             oy_param_table_name='completed',
                                             ox_lower_limit=self.__events_per_second_lower_limit,
                                             ox_upper_limit=self.__events_per_second_upper_limit,
                                             type=type,
                                             path=path)

    def create_total_losses_eps_graph(self, type, path=REPORT_DIR):
        self._template_comparative_aproximated_graph(ox_param_table_name='eps',
                                                     oy_param_table_name='completed',
                                                     type=type,
                                                     path=path)

    def get_event_latecy_rating(self,
                                raw_table,
                                multiplier=10**(0),
                                accuracy=3,
                                auto_normalize=True):

        ox_lst = raw_table['eps'].values.tolist()
        oy_lst = raw_table['latency'].values.tolist()

        if auto_normalize:

            oy_lst = [0] + oy_lst + [100]

            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(oy_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]
            if len(set(normalized_data_list)) == 1:
                normalized_data_list = [1.0 for _ in list(normalized_data_2d_array)]
            # print(normalized_data_list)

            func_latency = self._data_aproximation(ox_lst, normalized_data_list)
            i_latency, err = integrate.quad(func_latency,
                                            self.__events_per_second_lower_limit,
                                            self.__events_per_second_upper_limit-self.__events_per_second_step)

            return i_latency * multiplier
        else:
            func_latency = self._data_aproximation(ox_lst, oy_lst)
            i_latency, err = integrate.quad(func_latency,
                                            self.__events_per_second_lower_limit,
                                            self.__events_per_second_upper_limit,)
            try:
                return round(i_latency * multiplier, accuracy)
            except ZeroDivisionError:
                return 0

    def get_event_losses_rating(self,
                                raw_table,
                                multiplier=10**(0),
                                accuracy=3,
                                auto_normalize=True):

        ox_lst = raw_table['eps'].values.tolist()
        oy_lst = raw_table['completed'].values.tolist()

        if auto_normalize:

            oy_lst = [0] + oy_lst + [100]

            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(oy_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]
            if len(set(normalized_data_list)) == 1:
                normalized_data_list = [1.0 for _ in list(normalized_data_2d_array)]
            # print(normalized_data_list)

            func_completed = self._data_aproximation(ox_lst, normalized_data_list)
            i_completed, err = integrate.quad(func_completed,
                                          self.__events_per_second_lower_limit,
                                          self.__events_per_second_upper_limit-self.__events_per_second_step)
            return i_completed * multiplier
        else:
            func_completed = self._data_aproximation(ox_lst, oy_lst)
            i_completed, err = integrate.quad(func_completed,
                                              self.__events_per_second_lower_limit,
                                              self.__events_per_second_upper_limit,)
            try:
                return round(i_completed * multiplier, accuracy)
            except ZeroDivisionError:
                return 0

    def get_total_latency_rating(self, path=REPORT, accuracy=3):
        total_latency_rating = 0
        for event in self.__event_names:
            event_rating = self.get_event_latecy_rating(self.__main_raw_tables[event])
            with open(path, 'a+') as report:
                report.write('{} latency rating: {}\n'.format(event, event_rating))
            total_latency_rating += event_rating

        with open(path, 'a+') as report:
            report.write('total latency rating: {}\n'.format(round(total_latency_rating, accuracy)))
        return round(total_latency_rating, accuracy)

    def get_total_losses_rating(self, path=REPORT, accuracy=3):
        total_losses_rating = 0
        for event in self.__event_names:
            event_rating = self.get_event_losses_rating(self.__main_raw_tables[event])
            with open(path, 'a+') as report:
                report.write('{} losses rating: {}\n'.format(event, event_rating))
            total_losses_rating += event_rating

        with open(path, 'a+') as report:
            report.write('total losses rating: {}\n'.format(round(total_losses_rating, accuracy)))
        return round(total_losses_rating, accuracy)

    def get_total_auditd_rating(self,
                                path=REPORT,
                                multiplier=10**(2),
                                accuracy=0):
        # weight coefficients
        clat = 0.5
        clos = 1

        try:
            total_auditd_rating = round((clat * self.get_total_latency_rating(path=path))**(-1) * \
                                        (clos * self.get_total_losses_rating(path=path)) * \
                                        multiplier,
                                        accuracy)
        except Exception:
            total_auditd_rating = round((clos * self.get_total_losses_rating(path=path)) * multiplier, accuracy)

        with open(path, 'a+') as report:
            report.write('total auditd rating: {}\n'.format(total_auditd_rating))
        return total_auditd_rating
