import pandas
import tarfile
import warnings
import numpy as np
from numpy.exceptions import RankWarning
from shutil import copy
from time import time
from os import listdir
from libs.libpsb import log_in
from scipy import integrate
from sklearn import preprocessing
from matplotlib import pyplot as plt
from libs.libpsb import astra_version
from pretty_html_table import build_table
from psb_conf import SCALE_FACTOR, SCALE_FACTOR_STEP, LIMITE_SCALE_FACTOR, \
    TRANSACTIONS, TRANSACTIONS_STEP, LIMITE_TRANSACTIONS, \
    THREADS, THREADS_STEP, LIMITE_THREADS, \
    CLIENTS, CLIENTS_STEP, LIMITE_CLIENTS, STEP_RATIO_BY_CLIENTS, \
    REPORT_FILENAME, REPORT_PATH, LOG_FILENAME, REPORT_SYSMON_FILENAME


class Report:

    def __init__(self, param_name='clients',
                 report_file=REPORT_FILENAME,
                 all_params=False,
                 sysmon=False):

        self._report_file = report_file
        with open(self._report_file, 'r') as file:
            raw_data = file.read().split()

        if all_params:
            self.scales_lst = [int(param) for param in raw_data[0::9]]
            self.transactions_lst = [int(param) for param in raw_data[1::9]]
            self.threads_lst = [int(param) for param in raw_data[2::9]]
            self.clients_lst = [int(param) for param in raw_data[3::9]]
            self.la_lst = [float(la) for la in raw_data[4::9]]
            self.tps1_lst = [float(tps1) for tps1 in raw_data[5::9]]
            self.tps2_lst = [float(tps2) for tps2 in raw_data[6::9]]
            self.com_tr_lst = [int(c_trs) for c_trs in raw_data[7::9]]
            self.exp_tr_lst = [int(e_trs) for e_trs in raw_data[8::9]]
            self.raw_table = pandas.DataFrame({'scale': self.scales_lst,
                                               'transactions': self.transactions_lst,
                                               'threads': self.threads_lst,
                                               'clients': self.clients_lst,
                                               'la': self.la_lst,
                                               'tps1': self.tps1_lst,
                                               'tps2': self.tps2_lst,
                                               'com_tr': self.com_tr_lst,
                                               'exp_tr': self.exp_tr_lst})
        else:
            self.param_lst = [int(param) for param in raw_data[0::6]]
            self.la_lst = [float(la) for la in raw_data[1::6]]
            self.tps1_lst = [float(tps1) for tps1 in raw_data[2::6]]
            self.tps2_lst = [float(tps2) for tps2 in raw_data[3::6]]
            self.com_tr_lst = [int(c_trs) for c_trs in raw_data[4::6]]
            self.exp_tr_lst = [int(e_trs) for e_trs in raw_data[5::6]]

            # ---100-500/100---
            # for lst in (self.param_lst, self.la_lst, self.tps1_lst, self.tps2_lst, self.com_tr_lst, self.exp_tr_lst):
            #     if lst == self.param_lst:
            #         self.param_lst = [lst[9], lst[19], lst[29], lst[39], lst[49]]
            #     elif lst == self.tps1_lst:
            #         self.tps1_lst = [lst[9], lst[19], lst[29], lst[39], lst[49]]
            #     elif lst == self.tps2_lst:
            #         self.tps2_lst = [lst[9], lst[19], lst[29], lst[39], lst[49]]
            #     elif lst == self.la_lst:
            #         self.la_lst = [lst[9], lst[19], lst[29], lst[39], lst[49]]
            #     elif lst == self.com_tr_lst:
            #         self.com_tr_lst = [lst[9], lst[19], lst[29], lst[39], lst[49]]
            #     elif lst == self.exp_tr_lst:
            #         self.exp_tr_lst = [lst[9], lst[19], lst[29], lst[39], lst[49]]

            # ---100-300/50---
            # for lst in (self.param_lst, self.la_lst, self.tps1_lst, self.tps2_lst, self.com_tr_lst, self.exp_tr_lst):
            #     if lst == self.param_lst:
            #         self.param_lst = [lst[9], lst[14], lst[19], lst[24], lst[29]]
            #     elif lst == self.tps1_lst:
            #         self.tps1_lst = [lst[9], lst[14], lst[19], lst[24], lst[29]]
            #     elif lst == self.tps2_lst:
            #         self.tps2_lst = [lst[9], lst[14], lst[19], lst[24], lst[29]]
            #     elif lst == self.la_lst:
            #         self.la_lst = [lst[9], lst[14], lst[19], lst[24], lst[29]]
            #     elif lst == self.com_tr_lst:
            #         self.com_tr_lst = [lst[9], lst[14], lst[19], lst[24], lst[29]]
            #     elif lst == self.exp_tr_lst:
            #         self.exp_tr_lst = [lst[9], lst[14], lst[19], lst[24], lst[29]]

            # ---100-300/10---
            # for lst in (self.param_lst, self.la_lst, self.tps1_lst, self.tps2_lst, self.com_tr_lst, self.exp_tr_lst):
            #     if lst == self.param_lst:
            #         self.param_lst = lst[9:29]
            #     elif lst == self.tps1_lst:
            #         self.tps1_lst = lst[9:29]
            #     elif lst == self.tps2_lst:
            #         self.tps2_lst = lst[9:29]
            #     elif lst == self.la_lst:
            #         self.la_lst = lst[9:29]
            #     elif lst == self.com_tr_lst:
            #         self.com_tr_lst = lst[9:29]
            #     elif lst == self.exp_tr_lst:
            #         self.exp_tr_lst = lst[9:29]

            # ---50-250/10---
            # for lst in (self.param_lst, self.la_lst, self.tps1_lst, self.tps2_lst, self.com_tr_lst, self.exp_tr_lst):
            #     if lst == self.param_lst:
            #         self.param_lst = lst[4:25]
            #     elif lst == self.tps1_lst:
            #         self.tps1_lst = lst[4:25]
            #     elif lst == self.tps2_lst:
            #         self.tps2_lst = lst[4:25]
            #     elif lst == self.la_lst:
            #         self.la_lst = lst[4:25]
            #     elif lst == self.com_tr_lst:
            #         self.com_tr_lst = lst[4:25]
            #     elif lst == self.exp_tr_lst:
            #         self.exp_tr_lst = lst[4:25]

            # ---20-400/20---
            # for lst in (self.param_lst, self.la_lst, self.tps1_lst, self.tps2_lst, self.com_tr_lst, self.exp_tr_lst):
            #     if lst == self.param_lst:
            #         self.param_lst = lst[1:40:2]
            #     elif lst == self.tps1_lst:
            #         self.tps1_lst = lst[1:40:2]
            #     elif lst == self.tps2_lst:
            #         self.tps2_lst = lst[1:40:2]
            #     elif lst == self.la_lst:
            #         self.la_lst = lst[1:40:2]
            #     elif lst == self.com_tr_lst:
            #         self.com_tr_lst = lst[1:40:2]
            #     elif lst == self.exp_tr_lst:
            #         self.exp_tr_lst = lst[1:40:2]

            self.raw_table = pandas.DataFrame({param_name: self.param_lst,
                                               'la': self.la_lst,
                                               'tps1': self.tps1_lst,
                                               'tps2': self.tps2_lst,
                                               'com_tr': self.com_tr_lst,
                                               'exp_tr': self.exp_tr_lst})

            #print(self.raw_table)

        if sysmon:
            with open(f'{REPORT_SYSMON_FILENAME}', 'r') as report_sysmon_file:
                raw_sysmon_data = report_sysmon_file.read().split()
            self.avg_cpu = [int(float(avg_c)) for avg_c in raw_sysmon_data[0::4]]
            self.avg_mem = [int(float(avg_m)) for avg_m in raw_sysmon_data[1::4]]
            self.avg_pmem = [float(avg_pm) for avg_pm in raw_sysmon_data[2::4]]
            self.avg_disk = [int(float(avg_d)) for avg_d in raw_sysmon_data[3::4]]
            self.raw_sysmon_table = pandas.DataFrame({'avg_cpu %': self.avg_cpu,
                                                      'avg_mem %': self.avg_mem,
                                                      'avg_psql_mem %': self.avg_pmem,
                                                      'avg_disk %': self.avg_disk})
            self.raw_table = pandas.concat([self.raw_table, self.raw_sysmon_table], axis=1)

        # added column with result (% completed transactions)
        self.raw_table['result (%)'] = round(self.raw_table['com_tr'] / self.raw_table['exp_tr'] * 100, 2)

        # graph size
        self.width = 27
        self.height = 15

    @staticmethod
    def data_from_file(report_file=REPORT_FILENAME):
        with open(report_file, 'r') as file:
            raw_data = file.read().split()
        log_in('data_from_file', 
               ([int(param) for param in raw_data[0::6]],
                [float(la) for la in raw_data[1::6]],  # latency average data
                [float(tps1) for tps1 in raw_data[2::6]],  # tps including connections establishing data
                [float(tps2) for tps2 in raw_data[3::6]],  # tps excluding connections establishing data
                [float(c_trs) for c_trs in raw_data[4::6]],  # completed transactions
                [float(e_trs) for e_trs in raw_data[5::6]]))
        return ([int(param) for param in raw_data[0::6]],
                [float(la) for la in raw_data[1::6]],  # latency average data
                [float(tps1) for tps1 in raw_data[2::6]],  # tps including connections establishing data
                [float(tps2) for tps2 in raw_data[3::6]],  # tps excluding connections establishing data
                [float(c_trs) for c_trs in raw_data[4::6]],  # completed transactions
                [float(e_trs) for e_trs in raw_data[5::6]])  # expected transactions

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
                    log_in('data_aproximation', np.poly1d(np.polyfit(np.array(x), np.array(y), polinom_factor)))
                    return np.poly1d(np.polyfit(np.array(x), np.array(y), polinom_factor))
                except RankWarning:
                    polinom_factor -= 1

    @staticmethod
    def cm_to_inch(value):
        return value / 2.54

    @staticmethod
    def last_passed(lst1, lst2):
        for index in range(len(lst1)):
            if lst1[index] != lst2[index]:
                return index

    def create_beauty_table(self, path=REPORT_PATH, table_name='psb_report_table.html'):
        beauty_table = build_table(self.raw_table, 'blue_light')
        with open('{}/{}'.format(path, table_name), 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)

    def template_aproximated_graph(self,
                                   ox_param_table_name=None,
                                   ox_lst=None,
                                   ox_lower_limit=None,
                                   ox_upper_limit=None,
                                   oy_param_table_name=None,
                                   oy_lst=None,
                                   double_tps_graph=False,
                                   path=REPORT_PATH):

        if not double_tps_graph:
            '''
                Шаблон графика с апроксимацией и точками
            '''
            # points
            x = self.raw_table.loc[:, [ox_param_table_name]]
            y = self.raw_table.loc[:, [oy_param_table_name]]

            # build function f(x)
            aprx_x = np.arange(ox_lower_limit, ox_upper_limit, 0.1)
            aprx_f = self.data_aproximation(ox_lst, oy_lst)

            # build graph
            plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
            plt.plot(x, y, 'o')
            plt.plot(aprx_x, aprx_f(aprx_x))
            plt.title('{digit_varsion}({mode}). {ytitle}/{xtitle}'.format(digit_varsion=astra_version()[0],
                                                                          mode=astra_version()[1],
                                                                          xtitle=ox_param_table_name,
                                                                          ytitle=oy_param_table_name))
            plt.xlabel(ox_param_table_name)
            plt.xticks(ox_lst, ox_lst, rotation='vertical')
            plt.ylabel('{}'.format(oy_param_table_name))
            plt.grid(True)

            # colorized 100% zone
            last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
            plt.fill_between(ox_lst[0:last_passed_test],
                             oy_lst[0:last_passed_test],
                             color='palegreen')

            plt.savefig('{p}/psb_{ox}_{oy}_graph'.format(p=path,
                                                         ox=ox_param_table_name,
                                                         oy=oy_param_table_name))
        else:
            '''
                Шаблон графика с апроксимацией и точками для сравнения tps1 и tps2
            '''
            # build function f(x)
            aprx_x = np.arange(ox_lower_limit, ox_upper_limit, 0.1)
            aprx_f1 = self.data_aproximation(ox_lst, self.tps1_lst)
            aprx_f2 = self.data_aproximation(ox_lst, self.tps2_lst)

            # build graph
            plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
            plt.plot(aprx_x, aprx_f1(aprx_x))
            plt.plot(aprx_x, aprx_f2(aprx_x))
            plt.title('{digit_varsion}({mode}). TPS/{xtitle}'.format(digit_varsion=astra_version()[0],
                                                                     mode=astra_version()[1],
                                                                     xtitle=ox_param_table_name))
            plt.legend(['TPS(including connections establishing)', 'TPS(excluding connections establishing)'])
            plt.xlabel(ox_param_table_name)
            plt.xticks(ox_lst, ox_lst, rotation='vertical')
            plt.ylabel('TPS')
            plt.grid(True)

            # colorized 100% zone
            last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
            plt.fill_between(ox_lst[0:last_passed_test],
                             self.tps1_lst[0:last_passed_test],
                             color='palegreen')

            plt.savefig('{p}/psb_{ox}_tpsall_graph'.format(p=path,
                                                           ox=ox_param_table_name))

    def create_sysmon_graph(self,
                            x,
                            y,
                            filename,
                            title_graph,
                            x_rlim,
                            x_label="Tsec",
                            y_label=""):
        '''
            Построить граф для данных системного мониторинга
        '''
        aprx_x = np.arange(x[0], x[-1], 0.1)
        aprx_f = self.data_aproximation(x, y)

        # build graph
        fig, ax = plt.subplots(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        ax.plot(x, y, aprx_x, aprx_f(aprx_x))
        ax.set_yscale("linear")
        ax.set_xlim(0, x_rlim)
        ax.set_title(title_graph)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.grid(True)

        # fig.savefig(f'{filename}.png')
        fig.savefig('{path}/{file_name}.png'.format(path=REPORT_PATH, file_name=filename))
        # return "{file_name}.png".format(file_name=filename)

    '''
        Графики для теста: 'Нахождение предельного коэффициента масштаба' 
    '''
    def create_psb_sc_la_graph(self, report_dir=REPORT_PATH):
        self.template_aproximated_graph(ox_param_table_name='scale',
                                        ox_lst=self.scales_lst,
                                        ox_lower_limit=SCALE_FACTOR,
                                        ox_upper_limit=LIMITE_SCALE_FACTOR,
                                        oy_param_table_name='la',
                                        oy_lst=self.la_lst,
                                        path=report_dir)

    def create_psb_sc_tps1_graph(self, report_dir=REPORT_PATH):
        self.template_aproximated_graph(ox_param_table_name='scale',
                                        ox_lst=self.scales_lst,
                                        ox_lower_limit=SCALE_FACTOR,
                                        ox_upper_limit=LIMITE_SCALE_FACTOR,
                                        oy_param_table_name='tps1',
                                        oy_lst=self.tps1_lst,
                                        path=report_dir)

    def create_psb_sc_tps2_graph(self, report_dir=REPORT_PATH):
        self.template_aproximated_graph(ox_param_table_name='scale',
                                        ox_lst=self.scales_lst,
                                        ox_lower_limit=SCALE_FACTOR,
                                        ox_upper_limit=LIMITE_SCALE_FACTOR,
                                        oy_param_table_name='tps2',
                                        oy_lst=self.tps2_lst,
                                        path=report_dir)

    def create_psb_sc_tpsall_graph(self, report_dir=REPORT_PATH):
        self.template_aproximated_graph(ox_param_table_name='scale',
                                        ox_lst=self.scales_lst,
                                        ox_lower_limit=SCALE_FACTOR,
                                        ox_upper_limit=LIMITE_SCALE_FACTOR,
                                        double_tps_graph=True,
                                        path=report_dir)

    '''
        Графики для теста: 'Нахождение предельного числа транзакций' 
    '''
    def create_psb_tr_la_graph(self):
        self.template_aproximated_graph(ox_param_table_name='transactions',
                                        ox_lst=self.transactions_lst,
                                        ox_lower_limit=TRANSACTIONS,
                                        ox_upper_limit=LIMITE_TRANSACTIONS,
                                        oy_param_table_name='la',
                                        oy_lst=self.la_lst)

    def create_psb_tr_tps1_graph(self):
        self.template_aproximated_graph(ox_param_table_name='transactions',
                                        ox_lst=self.transactions_lst,
                                        ox_lower_limit=TRANSACTIONS,
                                        ox_upper_limit=LIMITE_TRANSACTIONS,
                                        oy_param_table_name='tps1',
                                        oy_lst=self.tps1_lst)

    def create_psb_tr_tps2_graph(self):
        self.template_aproximated_graph(ox_param_table_name='transactions',
                                        ox_lst=self.transactions_lst,
                                        ox_lower_limit=TRANSACTIONS,
                                        ox_upper_limit=LIMITE_TRANSACTIONS,
                                        oy_param_table_name='tps2',
                                        oy_lst=self.tps2_lst)

    def create_psb_tr_tpsall_graph(self):
        self.template_aproximated_graph(ox_param_table_name='transactions',
                                        ox_lst=self.transactions_lst,
                                        ox_lower_limit=TRANSACTIONS,
                                        ox_upper_limit=LIMITE_TRANSACTIONS,
                                        double_tps_graph=True)

    '''
        Графики для теста: 'Нахождение предельного числа потоков' 
    '''
    def create_psb_th_la_graph(self):
        self.template_aproximated_graph(ox_param_table_name='threads',
                                        ox_lst=self.transactions_lst,
                                        ox_lower_limit=THREADS,
                                        ox_upper_limit=LIMITE_THREADS,
                                        oy_param_table_name='la',
                                        oy_lst=self.la_lst)

    def create_psb_th_tps1_graph(self):
        self.template_aproximated_graph(ox_param_table_name='threads',
                                        ox_lst=self.transactions_lst,
                                        ox_lower_limit=THREADS,
                                        ox_upper_limit=LIMITE_THREADS,
                                        oy_param_table_name='tps1',
                                        oy_lst=self.tps1_lst)

    def create_psb_th_tps2_graph(self):
        self.template_aproximated_graph(ox_param_table_name='threads',
                                        ox_lst=self.transactions_lst,
                                        ox_lower_limit=THREADS,
                                        ox_upper_limit=LIMITE_THREADS,
                                        oy_param_table_name='tps2',
                                        oy_lst=self.tps2_lst)

    def create_psb_th_tpsall_graph(self):
        self.template_aproximated_graph(ox_param_table_name='threads',
                                        ox_lst=self.transactions_lst,
                                        ox_lower_limit=THREADS,
                                        ox_upper_limit=LIMITE_THREADS,
                                        double_tps_graph=True)


    '''
        Графики для теста: 'Нахождение предельного числа клиентов' 
    '''
    def create_psb_cl_la_graph(self, report_dir=REPORT_PATH):
        self.template_aproximated_graph(ox_param_table_name='clients',
                                        ox_lst=self.param_lst,
                                        ox_lower_limit=CLIENTS,
                                        ox_upper_limit=LIMITE_CLIENTS,
                                        oy_param_table_name='la',
                                        oy_lst=self.la_lst,
                                        path=report_dir)

    def create_psb_cl_tps1_graph(self, report_dir=REPORT_PATH):
        self.template_aproximated_graph(ox_param_table_name='clients',
                                        ox_lst=self.param_lst,
                                        ox_lower_limit=CLIENTS,
                                        ox_upper_limit=LIMITE_CLIENTS,
                                        oy_param_table_name='tps1',
                                        oy_lst=self.tps1_lst,
                                        path=report_dir)

    def create_psb_cl_tps2_graph(self, report_dir=REPORT_PATH):
        self.template_aproximated_graph(ox_param_table_name='clients',
                                        ox_lst=self.param_lst,
                                        ox_lower_limit=CLIENTS,
                                        ox_upper_limit=LIMITE_CLIENTS,
                                        oy_param_table_name='tps2',
                                        oy_lst=self.tps2_lst,
                                        path=report_dir)

    def create_psb_cl_tpsall_graph(self, report_dir=REPORT_PATH):
        self.template_aproximated_graph(ox_param_table_name='clients',
                                        ox_lst=self.param_lst,
                                        ox_lower_limit=CLIENTS,
                                        ox_upper_limit=LIMITE_CLIENTS,
                                        double_tps_graph=True,
                                        path=report_dir)

    def get_la_rating(self,
                      lower_limit=CLIENTS,
                      upper_limit=LIMITE_CLIENTS,
                      multiplier=10**(1),
                      accuracy=3,
                      auto_normalize=True):
        if auto_normalize:
            temp_lst = [0] + self.la_lst + [700]
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(temp_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]

            func_la = self.data_aproximation(self.param_lst, normalized_data_list)
            Ila, err = integrate.quad(func_la, lower_limit, upper_limit)
        else:
            func_la = self.data_aproximation(self.param_lst, self.la_lst)
            Ila, err = integrate.quad(func_la, lower_limit, upper_limit)

        log_in('get_la_rating', round((Ila * multiplier), accuracy))
        return round((Ila * multiplier), accuracy)

    def get_tps1_rating(self,
                        lower_limit=CLIENTS,
                        upper_limit=LIMITE_CLIENTS,
                        multiplier=10**(1),
                        accuracy=3,
                        auto_normalize=True):
        if auto_normalize:
            temp_lst = [0] + self.tps1_lst + [140000]
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(temp_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]

            func_tps1 = self.data_aproximation(self.param_lst, normalized_data_list)
            Itps1, err = integrate.quad(func_tps1, lower_limit, upper_limit)
        else:
            func_tps1 = self.data_aproximation(self.param_lst, self.tps1_lst)
            Itps1, err = integrate.quad(func_tps1, lower_limit, upper_limit)
        log_in('get_tps1_rating', round((Itps1 * multiplier), accuracy))
        return round((Itps1 * multiplier), accuracy)

    def get_tps2_rating(self,
                        lower_limit=CLIENTS,
                        upper_limit=LIMITE_CLIENTS,
                        multiplier=10**(1),
                        accuracy=3,
                        auto_normalize=True):
        if auto_normalize:
            temp_lst = [0] + self.tps1_lst + [140000]
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(temp_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]

            func_tps2 = self.data_aproximation(self.param_lst, normalized_data_list)
            Itps2, err = integrate.quad(func_tps2, lower_limit, upper_limit)
        else:
            func_tps2 = self.data_aproximation(self.param_lst, self.tps2_lst)
            Itps2, err = integrate.quad(func_tps2, lower_limit, upper_limit)
        log_in('get_tps2_rating', round((Itps2 * multiplier), accuracy))
        return round((Itps2 * multiplier), accuracy)

    def get_total_rating(self,
                         lower_limit=CLIENTS,
                         upper_limit=LIMITE_CLIENTS,
                         multiplier=10**(0),
                         accuracy=3):

        # weight coefficients
        c_la = 1 / 3
        c_tps1 = 2.5 / 3
        c_tps2 = 2.5 / 3

        log_in('get_total_rating', 
               abs(round((c_la * self.get_la_rating(lower_limit, upper_limit))**(-1)
                         + (c_tps1 * self.get_tps1_rating(lower_limit, upper_limit))
                         + (c_tps2 * self.get_tps2_rating(lower_limit, upper_limit))
                         * multiplier,
                         accuracy)))
        return abs(round((c_la * self.get_la_rating(lower_limit, upper_limit))**(-1)
                         + (c_tps1 * self.get_tps1_rating(lower_limit, upper_limit))
                         + (c_tps2 * self.get_tps2_rating(lower_limit, upper_limit))
                         * multiplier,
                         accuracy))

    def merge(self, table_lst, graph_lst, path=REPORT_PATH):
        '''
            Создать HTML
        '''

        tables_in_total_html = []
        for table in table_lst:
            with open('{}/{}'.format(path, table)) as file:
                tables_in_total_html.append('<div class="table_block">{}</div>\n'.format(file.read()))

        graphs_in_total_html = []
        for graph in graph_lst:
            graphs_in_total_html.append('<div class="graph_block"><img src="{}"></div>\n'.format(graph))

        html_template_part1 = [
            '<!DOCTYPE html>\n',
            '<html>\n',
            '  <head>\n',
            '    <meta charset="utf-8">\n',
            '    <title>PostgreSQL report</title>\n',
            '    <link href="./style.css" rel="stylesheet" type="text/css">\n',
            '  </head>\n',
            '  <body>\n',
            '    <style>\n',
            '        .line_block {\n',
            '                width:48%;\n',
            '                height:100%;\n',
            '                background:#f1f1f1;\n',
            '                float:left;\n',
            '                margin: 0 15px 15px 0;\n',
            '                text-align:center;\n',
            '                padding: 0.7%;\n',
            '                }\n',
            '        .table_block {\n',
            '                width:95%;\n',
            '                height:100%;\n',
            '                background:#4169E1;\n',
            '                float:left;\n',
            '                margin: auto;\n',
            '                text-align:center;\n',
            '                padding: 0.7%;\n',
            '                }\n',
            '        .graph_block {\n',
            '                width:95%;\n',
            '                height:100%;\n',
            '                background:#4169E1;\n',
            '                margin: auto;\n',
            '                text-align:center;\n',
            '                padding: 0.7%;\n',
            '                }\n',
            '        .dataframe {\n',
            '                margin: auto;\n',
            '                }\n',
            '    </style>\n',
            '    <div style = "width:50%; height:1px; clear:both;"></div>\n'
            '    <div style = "width:100%; background:#f1f1f1; float:left; margin: 0 15px 15px 0; padding: 10px;">\n',
            '    <h1>Stress testing PostgreSQL</h1>\n',
            '    </div>\n',
            '    <div style="width:50%; height:1px; clear:both;"></div>\n',
            '    <div class="line_block">\n',
        ]

        html_template_part2 = [
            '    </div>\n',
            '    <div class="line_block">Total rating: {} elefants</div>\n'.format(self.get_total_rating()),
            '    <div class="line_block">\n',
        ]

        html_template_part3 = [
            '    </div>\n',
            '  </body>\n',
            '</html>\n'
        ]

        with open('{}/main_report.html'.format(path), 'w') as total_html:
            total_html.writelines(html_template_part1)
            total_html.writelines(tables_in_total_html)
            total_html.writelines(html_template_part2)
            total_html.writelines(graphs_in_total_html)
            total_html.writelines(html_template_part3)

    @staticmethod
    def create_tar(path=REPORT_PATH):
        '''
            tar архив с результатами тестирования
        '''
        time_mark = time()
        copy(LOG_FILENAME, '{}/main_log'.format(path))
        with tarfile.open('report{v}_{m}_{t}.tar'.format(v=astra_version()[0],
                                                         m=astra_version()[1],
                                                         t=time_mark), 'w') as tar:
            for file in listdir(path):
                tar.add('{}/{}'.format('report', file))
