import pandas
import tarfile
import warnings
#import pdfkit
import numpy as np

from shutil import copy
from time import time
from os import listdir
from scipy import integrate
from matplotlib import pyplot as plt
from libs.libpsb import astra_version
from pretty_html_table import build_table
from psb_conf import SCALE_FACTOR, SCALE_FACTOR_STEP, LIMITE_SCALE_FACTOR, \
    TRANSACTIONS, TRANSACTIONS_STEP, LIMITE_TRANSACTIONS, \
    THREADS, THREADS_STEP, LIMITE_THREADS, \
    CLIENTS, CLIENTS_STEP, LIMITE_CLIENTS, STEP_RATIO_BY_CLIENTS, \
    REPORT_FILENAME, REPORT_PATH, LOG_FILENAME


class Report:

    def __init__(self, param_name='clients', report_file=REPORT_FILENAME, all_params=False):
        with open(report_file, 'r') as report_file:
            raw_data = report_file.read().split()
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
            self.raw_table = pandas.DataFrame({param_name: self.param_lst,
                                               'la': self.la_lst,
                                               'tps1': self.tps1_lst,
                                               'tps2': self.tps2_lst,
                                               'com_tr': self.com_tr_lst,
                                               'exp_tr': self.exp_tr_lst})

        # added column with result (% completed transactions)
        self.raw_table['result (%)'] = round(self.raw_table['com_tr'] / self.raw_table['exp_tr'] * 100, 2)

        # graph size
        self.width = 27
        self.height = 15

    @staticmethod
    def data_from_file(report_file=REPORT_FILENAME):
        with open(report_file, 'r') as file:
            raw_data = file.read().split()
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
                    return np.poly1d(np.polyfit(np.array(x), np.array(y), polinom_factor))
                except np.RankWarning:
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

    '''
        Графики для теста: 'Нахождение предельного числа клиентов' 
    '''
    def create_psb_cl_la_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['la']]

        aprx_x = np.arange(CLIENTS, LIMITE_CLIENTS, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.la_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Clients/Latency average'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Clients')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('Latency average')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.la_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_cl_la_graph'.format(path))

    def create_psb_cl_tps1_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['tps1']]

        aprx_x = np.arange(CLIENTS, LIMITE_CLIENTS, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.tps1_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Clients/TPS(including connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Clients')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps1_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_cl_tps1_graph'.format(path))

    def create_psb_cl_tps2_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['tps2']]

        aprx_x = np.arange(CLIENTS, LIMITE_CLIENTS, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.tps2_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Clients/TPS(excluding connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Clients')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps1_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_cl_tps2_graph'.format(path))

    def create_psb_cl_tpsall_graph(self, path=REPORT_PATH):

        aprx_x = np.arange(CLIENTS, LIMITE_CLIENTS, 0.1)
        aprx_f1 = self.data_aproximation(self.param_lst, self.tps1_lst)
        aprx_f2 = self.data_aproximation(self.param_lst, self.tps2_lst)
        # build graph

        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(aprx_x, aprx_f1(aprx_x))
        plt.plot(aprx_x, aprx_f2(aprx_x))
        plt.title('{}({}). Clients/TPS'.format(astra_version()[0], astra_version()[1]))
        plt.legend(['TPS(including connections establishing)', 'TPS(excluding connections establishing)'])
        plt.xlabel('Clients')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps1_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_cl_tpsall_graph'.format(path))

    '''
        Графики для теста: 'Нахождение предельного коэффициента масштаба' 
    '''
    def create_psb_sc_la_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['scale']]
        y = self.raw_table.loc[:, ['la']]

        aprx_x = np.arange(SCALE_FACTOR, LIMITE_SCALE_FACTOR, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.la_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Scale/Latency average'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Scale')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('Latency average')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.la_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_sc_la_graph'.format(path))

    def create_psb_sc_tps1_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['scale']]
        y = self.raw_table.loc[:, ['tps1']]

        aprx_x = np.arange(SCALE_FACTOR, LIMITE_SCALE_FACTOR, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.tps1_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Scale/TPS(including connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Scale')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps1_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_sc_tps1_graph'.format(path))

    def create_psb_sc_tps2_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['scale']]
        y = self.raw_table.loc[:, ['tps2']]

        aprx_x = np.arange(SCALE_FACTOR, LIMITE_SCALE_FACTOR, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.tps2_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Scale/TPS(excluding connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Scale')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps2_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_sc_tps2_graph'.format(path))

    def create_psb_sc_tpsall_graph(self, path=REPORT_PATH):

        aprx_x = np.arange(SCALE_FACTOR, LIMITE_SCALE_FACTOR, 0.1)
        aprx_f1 = self.data_aproximation(self.param_lst, self.tps1_lst)
        aprx_f2 = self.data_aproximation(self.param_lst, self.tps2_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(aprx_x, aprx_f1(aprx_x))
        plt.plot(aprx_x, aprx_f2(aprx_x))
        plt.title('{}({}). Scale/TPS'.format(astra_version()[0], astra_version()[1]))
        plt.legend(['TPS(including connections establishing)', 'TPS(excluding connections establishing)'])
        plt.xlabel('Scale')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps1_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_sc_tpsall_graph'.format(path))

    '''
        Графики для теста: 'Нахождение предельного числа транзакций' 
    '''
    def create_psb_tr_la_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['transactions']]
        y = self.raw_table.loc[:, ['la']]

        aprx_x = np.arange(TRANSACTIONS, LIMITE_TRANSACTIONS, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.la_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Transactions/Latency averege'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Transactions')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('Latency average')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.la_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_tr_la_graph'.format(path))

    def create_psb_tr_tps1_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['transactions']]
        y = self.raw_table.loc[:, ['tps1']]

        aprx_x = np.arange(TRANSACTIONS, LIMITE_TRANSACTIONS, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.tps1_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Transactions/TPS(including connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Transactions')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps1_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_tr_tps1_graph'.format(path))

    def create_psb_tr_tps2_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['transactions']]
        y = self.raw_table.loc[:, ['tps2']]

        aprx_x = np.arange(TRANSACTIONS, LIMITE_TRANSACTIONS, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.tps2_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Transactions/TPS(excluding connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Transactions')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps2_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_tr_tps2_graph'.format(path))

    def create_psb_tr_tpsall_graph(self, path=REPORT_PATH):

        aprx_x = np.arange(TRANSACTIONS, LIMITE_TRANSACTIONS, 0.1)
        aprx_f1 = self.data_aproximation(self.param_lst, self.tps1_lst)
        aprx_f2 = self.data_aproximation(self.param_lst, self.tps2_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(aprx_x, aprx_f1(aprx_x))
        plt.plot(aprx_x, aprx_f2(aprx_x))
        plt.title('{}({}). Transactions/TPS'.format(astra_version()[0], astra_version()[1]))
        plt.legend(['TPS(including connections establishing)', 'TPS(excluding connections establishing)'])
        plt.xlabel('Transactions')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps1_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_tr_tpsall_graph'.format(path))

    '''
        Графики для теста: 'Нахождение предельного числа потоков' 
    '''
    def create_psb_th_la_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['threads']]
        y = self.raw_table.loc[:, ['la']]

        aprx_x = np.arange(THREADS, LIMITE_THREADS, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.la_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Threads/Latency averege'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Threads')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('Latency average')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.la_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_th_la_graph'.format(path))

    def create_psb_th_tps1_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['threads']]
        y = self.raw_table.loc[:, ['tps1']]

        aprx_x = np.arange(THREADS, LIMITE_THREADS, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.tps1_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Threads/TPS(including connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Threads')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps1_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_th_tps1_graph'.format(path))

    def create_psb_th_tps2_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['threads']]
        y = self.raw_table.loc[:, ['tps2']]

        aprx_x = np.arange(THREADS, LIMITE_THREADS, 0.1)
        aprx_f = self.data_aproximation(self.param_lst, self.tps2_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o', aprx_x, aprx_f(aprx_x))
        plt.title('{}({}). Threads/TPS(excluding connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Threads')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps2_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_th_tps2_graph'.format(path))

    def create_psb_th_tpsall_graph(self, path=REPORT_PATH):

        aprx_x = np.arange(THREADS, LIMITE_THREADS, 0.1)
        aprx_f1 = self.data_aproximation(self.param_lst, self.tps1_lst)
        aprx_f2 = self.data_aproximation(self.param_lst, self.tps2_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(aprx_x, aprx_f1(aprx_x))
        plt.plot(aprx_x, aprx_f2(aprx_x))
        plt.title('{}({}). Threads/TPS'.format(astra_version()[0], astra_version()[1]))
        plt.legend(['TPS(including connections establishing)', 'TPS(excluding connections establishing)'])
        plt.xlabel('Threads')
        plt.xticks(self.param_lst, self.param_lst, rotation='vertical')
        plt.ylabel('TPS')
        plt.grid(True)

        # colorized 100% zone
        last_passed_test = self.last_passed(self.com_tr_lst, self.exp_tr_lst)
        plt.fill_between(self.param_lst[0:last_passed_test],
                         self.tps1_lst[0:last_passed_test],
                         color='palegreen')

        plt.savefig('{}/psb_th_tpsall_graph'.format(path))

    def get_la_rating(self, lower_limit=CLIENTS, upper_limit=LIMITE_CLIENTS):
        func_la = self.data_aproximation(self.param_lst, self.la_lst)
        Ila, err = integrate.quad(func_la, lower_limit, upper_limit)
        return 1 / Ila

    def get_tps1_rating(self, lower_limit=CLIENTS, upper_limit=LIMITE_CLIENTS):
        func_tps1 = self.data_aproximation(self.param_lst, self.tps1_lst)
        Itps1, err = integrate.quad(func_tps1, lower_limit, upper_limit)
        return 1 / Itps1

    def get_tps2_rating(self, lower_limit=CLIENTS, upper_limit=LIMITE_CLIENTS):
        func_tps2 = self.data_aproximation(self.param_lst, self.tps2_lst)
        Itps2, err = integrate.quad(func_tps2, lower_limit, upper_limit)
        return 1 / Itps2

    def get_total_rating(self, lower_limit=CLIENTS, upper_limit=LIMITE_CLIENTS, accuracy=10):
        return round((self.get_la_rating(lower_limit, upper_limit) + self.get_tps1_rating(lower_limit, upper_limit) + self.get_tps2_rating(lower_limit, upper_limit)), accuracy)*1000000

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
            '                margin: 1%;\n',
            '                text-align:center;\n',
            '                padding: 0.7%;\n',
            '                }\n',
            '        .graph_block {\n',
            '                width:95%;\n',
            '                height:100%;\n',
            '                background:#4169E1;\n',
            '                margin: 1%;\n',
            '                text-align:center;\n',
            '                padding: 0.7%;\n',
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
        #pdfkit.from_file('{}/main_report.html'.format(path),
        #                 'report{v}_{m}_{t}.pdf'.format(v=astra_version()[0],
        #                                                m=astra_version()[1],
        #                                                t=time_mark))
        with tarfile.open('report{v}_{m}_{t}.tar'.format(v=astra_version()[0],
                                                         m=astra_version()[1],
                                                         t=time_mark), 'w') as tar:
            for file in listdir(path):
                tar.add('{}/{}'.format('report', file))
