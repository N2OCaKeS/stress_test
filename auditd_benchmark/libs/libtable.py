import tarfile
import pandas
import warnings
import numpy as np

from time import time
from scipy import integrate
from os import listdir, chdir
from matplotlib import pyplot as plt
from libs.libaub import astra_version, astra_kernel_version
from pretty_html_table import build_table
from aub_conf import PROC_BODYS, SCRIPT_DIR, \
    LOG_DIR, REPORT_DIR, REPORT, \
    LATENCY_REPORT, LOSSES_REPORT, \
    DEFAULT_PS_LIFETIME, DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY, PS_LOWER_LIMIT, PS_UPPER_LIMIT


class Report:
    def __init__(self,
                 event_names=PROC_BODYS.keys(),
                 latency_report=LATENCY_REPORT,
                 losses_report=LOSSES_REPORT):

        self.__event_names = event_names
        self.__events_per_second_lower_limit = float(DEFAULT_PS_LIFETIME) / float(DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY) * int(PS_LOWER_LIMIT)
        self.__events_per_second_upper_limit = float(DEFAULT_PS_LIFETIME) / float(DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY) * int(PS_UPPER_LIMIT) - self.__events_per_second_lower_limit

        # graph size
        self.width = 27
        self.height = 15

        if latency_report:
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
            self.__main_raw_tables = {}
            for event in self.__event_names:
                self.__main_raw_tables[event] = pandas.concat([self.__losses_raw_tables[event],
                                                               self.__latency_raw_tables[event]['latency']], axis=1)
                self.__main_raw_tables[event].reset_index()
                print(self.__main_raw_tables[event])

    @staticmethod
    def _cm_to_inch(value):
        return value / 2.54

    @staticmethod
    def _last_passed(lst1, lst2):
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

    def create_beauty_table(self, path=REPORT_DIR, table_name='aub_{name}_table.html'):
        for event in self.__event_names:
            beauty_table = build_table(self.__main_raw_tables[event], 'blue_light')
            with open('{}/{}'.format(path, table_name.format(name=event)), 'w') as beauty_html_table:
                beauty_html_table.write(beauty_table)

    @staticmethod
    def create_tar():
        time_mark = time()
        with tarfile.open('aub_{v}_{m}_{k}_{t}.tar'.format(v=astra_version()[0],
                                                           m=astra_version()[1],
                                                           k=astra_kernel_version(),
                                                           t=time_mark), 'w') as tar:
            chdir(SCRIPT_DIR)
            for file in listdir(REPORT_DIR):
                tar.add('{}/{}'.format('report', file))

            for file in listdir(LOG_DIR):
                tar.add('{}/{}'.format('report', file))

    def _template_aproximated_graph(self,
                                    event=None,
                                    raw_table=None,
                                    ox_param_table_name=None,
                                    oy_param_table_name=None,
                                    ox_lower_limit=None,
                                    ox_upper_limit=None,
                                    path=None):

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
        aprx_x = np.arange(ox_lower_limit, ox_upper_limit, 1)
        aprx_f = self._data_aproximation(ox_lst, oy_lst)

        # build graph
        plt.figure(figsize=(self._cm_to_inch(self.width), self._cm_to_inch(self.height)))
        plt.plot(x, y, 'o')
        plt.plot(aprx_x, aprx_f(aprx_x))
        plt.title('{digit_varsion}({mode}). {ytitle}/{xtitle}'.format(digit_varsion=astra_version()[0],
                                                                      mode=astra_version()[1],
                                                                      xtitle=ox_param_table_name,
                                                                      ytitle=oy_param_table_name))

        # colorized 100% zone
        last_passed_test = self._last_passed(self.__expected_event_quantity_lst, self.__real_event_quantity_lst)
        plt.fill_between(ox_lst[0:last_passed_test],
                         oy_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{p}/aub_{ev}_{ox}_{oy}_graph'.format(p=path,
                                                          ev=event,
                                                          ox=ox_param_table_name,
                                                          oy=oy_param_table_name))

    def create_aub_latency_eps_graph(self, path=REPORT_DIR):
        for event in self.__event_names:
            self._template_aproximated_graph(event=event,
                                             ox_param_table_name='eps',
                                             oy_param_table_name='latency',
                                             ox_lower_limit=self.__events_per_second_lower_limit,
                                             ox_upper_limit=self.__events_per_second_upper_limit,
                                             path=path)

    def create_aub_losses_eps_graph(self, path=REPORT_DIR):
        for event in self.__event_names:
            self._template_aproximated_graph(event=event,
                                             ox_param_table_name='eps',
                                             oy_param_table_name='completed',
                                             ox_lower_limit=self.__events_per_second_lower_limit,
                                             ox_upper_limit=self.__events_per_second_upper_limit,
                                             path=path)

    def get_event_latecy_rating(self, raw_table, multiplier=10**3, accuracy=3):

        ox_lst = raw_table['eps'].values.tolist()
        oy_lst = raw_table['latency'].values.tolist()

        func_latency = self._data_aproximation(ox_lst, oy_lst)
        i_latency, err = integrate.quad(func_latency,
                                        self.__events_per_second_lower_limit,
                                        self.__events_per_second_upper_limit,)
        try:
            return round(1 / i_latency * multiplier, accuracy)
        except ZeroDivisionError:
            return 0

    def get_event_losses_rating(self, raw_table, multiplier=10**(-4), accuracy=3):

        ox_lst = raw_table['eps'].values.tolist()
        oy_lst = raw_table['completed'].values.tolist()

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

    def get_total_auditd_rating(self, path=REPORT, accuracy=3):
        total_auditd_rating = round(self.get_total_latency_rating(path=path) + \
                                    self.get_total_losses_rating(path=path),
                                    accuracy)
        with open(path, 'a+') as report:
            report.write('total auditd rating: {}\n'.format(total_auditd_rating))
        return total_auditd_rating

