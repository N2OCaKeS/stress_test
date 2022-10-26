import tarfile
import pandas
import warnings
import numpy as np

from time import time
from os import listdir, chdir
from matplotlib import pyplot as plt
from libs.libaub import astra_version, astra_kernel_version
from pretty_html_table import build_table
from aub_conf import PROC_BODYS, SCRIPT_DIR, \
    LOG_DIR, REPORT_DIR, \
    LATENCY_REPORT, LOSSES_REPORT


class Report:
    def __init__(self,
                 latency_report=LATENCY_REPORT,
                 losses_report=LOSSES_REPORT):

        if latency_report:
            with open(latency_report, 'r') as report_file:
                raw_data = report_file.read().split()

            self.__event_name_lst = [str(i) for i in raw_data[0::2]]
            self.__latency_lst = [float(i) for i in raw_data[1::2]]
            self.__raw_table = pandas.DataFrame({'audit_event': self.__event_name_lst,
                                                 'latency': self.__latency_lst})

            self.__latency_raw_tables = {}
            for event in PROC_BODYS.keys():
                self.__latency_raw_tables[event] = self.__raw_table[self.__raw_table.audit_event == event]

        if losses_report:
            with open(losses_report, 'r') as report_file:
                raw_data = report_file.read().split()

            self.__event_name_lst = [str(i) for i in raw_data[0::5]]
            self.__events_per_second_lst = [str(i) for i in raw_data[1::5]]
            self.__expected_event_quantity_lst = [int(i) for i in raw_data[2::5]]
            self.__real_event_quantity_lst = [int(i) for i in raw_data[3::5]]
            self.__result_in_percent_lst = [float(i) for i in raw_data[4::5]]
            self.__raw_table = pandas.DataFrame({'audit_event': self.__event_name_lst,
                                                 'eps': self.__events_per_second_lst,
                                                 'exp_ev': self.__expected_event_quantity_lst,
                                                 'real_ev': self.__real_event_quantity_lst,
                                                 'completed': self.__result_in_percent_lst})

            self.__losses_raw_tables = {}
            for event in PROC_BODYS.keys():
                self.__losses_raw_tables[event] = self.__raw_table[self.__raw_table.audit_event == event]

        if losses_report and latency_report:
            self.__main_raw_tables = {}
            for event in PROC_BODYS.keys():
                self.__main_raw_tables[event] = pandas.concat([self.__losses_raw_tables[event],
                                                               self.__latency_raw_tables[event]['latency']], axis=1)
                self.__main_raw_tables[event].reset_index()
                self.__main_raw_tables[event].drop(columns=['index'], axis=1)

        # graph size
        self.width = 27
        self.height = 15

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
        for event in PROC_BODYS.keys():
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
                                    raw_table,
                                    ox_param_table_name=None,
                                    ox_lst=None,
                                    ox_lower_limit=None,
                                    ox_upper_limit=None,
                                    oy_param_table_name=None,
                                    oy_lst=None,
                                    path=REPORT_DIR):

        # точки
        x = raw_table.loc[:, [ox_param_table_name]]
        y = raw_table.loc[:, [oy_param_table_name]]

        # построить аппроксимирующую f(x)
        aprx_x = np.arange(ox_lower_limit, ox_upper_limit, 0.1)
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

        plt.savefig('{p}/aub_{ox}_{oy}_graph'.format(p=path,
                                                     ox=ox_param_table_name,
                                                     oy=oy_param_table_name))

    def create_aub_latency_eps(self):
        self._template_aproximated_graph(ox_param_table_name='eps',
                                         ox_lst=self.__events_per_second_lst,
                                         ox_lower_limit=self.__events_per_second_lst[0],
                                         ox_upper_limit=self.__events_per_second_lst[1],
                                         oy_param_table_name='latency',
                                         oy_lst=self.__latency_lst)

    def create_aub_losses_eps(self):
        self._template_aproximated_graph(ox_param_table_name='eps',
                                         ox_lst=self.__events_per_second_lst,
                                         ox_lower_limit=self.__events_per_second_lst[0],
                                         ox_upper_limit=self.__events_per_second_lst[1],
                                         oy_param_table_name='completed',
                                         oy_lst=self.__result_in_percent_lst)
