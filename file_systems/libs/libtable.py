import os

import pandas
import tarfile
import warnings
import numpy as np

from shutil import copy
from time import time
from os import listdir
from scipy import integrate
from sklearn import preprocessing
from matplotlib import pyplot as plt
from libs.libfsb import astra_version
from pretty_html_table import build_table
from fsb_conf import REPORT_PATH, REPORT_FILENAME, LOG_PATH, SCRIPT_DIR, \
    FILES, FILES_STEP, FILES_LIMIT, \
    SIZE, SIZE_STEP, SIZE_LIMIT


class Report:
    def __init__(self,
                 ox_lo_lim,
                 ox_step,
                 ox_up_lim,
                 report=REPORT_PATH):

        '''
            :param report: path to report file
            read and parsing data from report file
        '''
        with open('{}/{}'.format(report, REPORT_FILENAME), 'r') as report_file:
            raw_data = report_file.read().split()
            self.fs_use_lst = [int(param) for param in raw_data[0::23]]  # percents
            self.file_count_lst = [int(param) for param in raw_data[1::23]]
            self.file_size_lst = [int(param) for param in raw_data[2::23]]
            self.speed_lst = [float(param) for param in raw_data[3::23]]
            self.app_overhead_lst = [int(param) for param in raw_data[4::23]]
            self.create_min_lst = [int(param) for param in raw_data[5::23]]
            self.create_avg_lst = [int(param) for param in raw_data[6::23]]
            self.create_max_lst = [int(param) for param in raw_data[7::23]]
            self.write_min_lst = [int(param) for param in raw_data[8::23]]
            self.write_avg_lst = [int(param) for param in raw_data[9::23]]
            self.write_max_lst = [int(param) for param in raw_data[10::23]]
            self.fsync_min_lst = [int(param) for param in raw_data[11::23]]
            self.fsync_avg_lst = [int(param) for param in raw_data[12::23]]
            self.fsync_max_lst = [int(param) for param in raw_data[13::23]]
            self.sync_min_lst = [int(param) for param in raw_data[14::23]]
            self.sync_avg_lst = [int(param) for param in raw_data[15::23]]
            self.sync_max_lst = [int(param) for param in raw_data[16::23]]
            self.close_min_lst = [int(param) for param in raw_data[17::23]]
            self.close_avg_lst = [int(param) for param in raw_data[18::23]]
            self.close_max_lst = [int(param) for param in raw_data[19::23]]
            self.unlink_min_lst = [int(param) for param in raw_data[20::23]]
            self.unlink_avg_lst = [int(param) for param in raw_data[21::23]]
            self.unlink_max_lst = [int(param) for param in raw_data[22::23]]

            self.raw_table = pandas.DataFrame({ 'fs_use': self.fs_use_lst,
                                                'file_count': self.file_count_lst,
                                                'file_size': self.file_size_lst,
                                                'speed': self.speed_lst,
                                                'app_overhead': self.app_overhead_lst,
                                                'create_min': self.create_min_lst,
                                                'create_avg': self.create_avg_lst,
                                                'create_max': self.create_max_lst,
                                                'write_min': self.write_min_lst,
                                                'write_avg': self.write_avg_lst,
                                                'write_max': self.write_max_lst,
                                                'fsync_min': self.fsync_min_lst,
                                                'fsync_avg': self.fsync_avg_lst,
                                                'fsync_max': self.fsync_max_lst,
                                                'sync_min': self.sync_min_lst,
                                                'sync_avg': self.sync_avg_lst,
                                                'sync_max': self.sync_max_lst,
                                                'close_min': self.close_min_lst,
                                                'close_avg': self.close_avg_lst,
                                                'close_max': self.close_max_lst,
                                                'unlink_min': self.unlink_min_lst,
                                                'unlink_avg': self.unlink_avg_lst,
                                                'unlink_max': self.unlink_max_lst})

            #print(self.raw_table)

        self.ox_lower_limit = ox_lo_lim
        self.ox_step = ox_step
        self.ox_upper_limit = ox_up_lim
        self._report_dir = report

        # graph size
        self.width = 27
        self.height = 15

        self.grid_factor = (self.ox_upper_limit - self.ox_lower_limit) // 10


    '''
        Создать html таблицу
    '''
    def create_beauty_table(self, path=REPORT_PATH, table_name='fsb_report_table.html'):
        beauty_table = build_table(self.raw_table, 'blue_light')
        with open('{}/{}'.format(path, table_name), 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)

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

    ####################################################################################################################
    def template_aproximated_graph(self,
                                   ox_param_table_name,
                                   ox_lst,
                                   oy_param_table_name,
                                   oy_lst,
                                   path=REPORT_PATH):
        '''
            Шаблон графика с апроксимацией и точками
        '''
        # points
        x = self.raw_table.loc[:, [ox_param_table_name]]
        y = self.raw_table.loc[:, [oy_param_table_name]]

        # build function f(x)
        aprx_x = np.arange(self.ox_lower_limit, self.ox_upper_limit, 1)
        aprx_f = self.data_aproximation(ox_lst, oy_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y, 'o'),
        plt.plot(aprx_x, aprx_f(aprx_x))
        plt.title('{digit_varsion}({mode}). {ytitle}/{xtitle}'.format(digit_varsion=astra_version()[0],
                                                                      mode=astra_version()[1],
                                                                      xtitle=ox_param_table_name,
                                                                      ytitle=oy_param_table_name))
        plt.xlabel(ox_param_table_name)
        ox_ticks = np.arange(self.ox_lower_limit,
                             self.ox_upper_limit,
                             self.grid_factor)
        plt.xticks(ox_ticks, ox_ticks, rotation='vertical')
        plt.ylabel('{}(msec)'.format(oy_param_table_name))
        plt.grid(True)

        plt.savefig('{p}/fsb_{ox}_{oy}_graph'.format(p=path,
                                                     ox=ox_param_table_name,
                                                     oy=oy_param_table_name))

    def template_syscall_graph(self,
                               ox_param_table_name,
                               ox_lst,
                               syscall,
                               path=REPORT_PATH):
        '''
            Шаблон графика на три кривые для системных вызовов
        '''
        # points
        x = self.raw_table.loc[:, [ox_param_table_name]]
        y1 = self.raw_table.loc[:, ['{}_min'.format(syscall)]]
        y2 = self.raw_table.loc[:, ['{}_avg'.format(syscall)]]
        y3 = self.raw_table.loc[:, ['{}_max'.format(syscall)]]

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        plt.plot(x, y1, 'g')
        plt.plot(x, y2, 'y')
        plt.plot(x, y3, 'r')
        plt.title('{digit_varsion}({mode}). {title1}/{title2}'.format(digit_varsion=astra_version()[0],
                                                                      mode=astra_version()[1],
                                                                      title1=ox_param_table_name,
                                                                      title2=syscall.upper()))
        plt.legend(['{} min'.format(syscall.upper()),
                    '{} avg'.format(syscall.upper()),
                    '{} max'.format(syscall.upper())])
        plt.xlabel(ox_param_table_name)
        ox_ticks = np.arange(self.ox_lower_limit,
                             self.ox_upper_limit,
                             self.grid_factor)
        plt.xticks(ox_ticks, ox_ticks, rotation='vertical')
        plt.ylabel('syscall {}(msec)'.format(syscall.upper()))
        plt.grid(True)

        plt.savefig('{}/fsb_{}_{}_graph'.format(path,
                                                ox_param_table_name,
                                                syscall))

    def create_fsb_fc_sp_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости работы от количества файлов
        '''
        self.template_aproximated_graph(ox_param_table_name='file_count',
                                        ox_lst=self.file_count_lst,
                                        oy_param_table_name='speed',
                                        oy_lst=self.speed_lst,
                                        path=path)

    def create_fsb_fc_app_overhead_graph(self, path=REPORT_PATH):
        '''
            График зависимости накладных расходов(мсек) от количества файлов.
            Без выполнения системных вызовов, связанных с записью файла.
        '''
        self.template_aproximated_graph(ox_param_table_name='file_count',
                                        ox_lst=self.file_count_lst,
                                        oy_param_table_name='app_overhead',
                                        oy_lst=self.app_overhead_lst,
                                        path=path)

    def create_fsb_sz_sp_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости работы от размера файлов
        '''
        self.template_aproximated_graph(ox_param_table_name='file_size',
                                        ox_lst=self.file_size_lst,
                                        oy_param_table_name='speed',
                                        oy_lst=self.speed_lst,
                                        path=path)

    def create_fsb_sz_app_overhead_graph(self, path=REPORT_PATH):
        '''
            График зависимости накладных расходов(мсек) от размера файлов.
            Без выполнения системных вызовов, связанных с записью файла.
        '''
        self.template_aproximated_graph(ox_param_table_name='file_size',
                                        ox_lst=self.file_size_lst,
                                        oy_param_table_name='app_overhead',
                                        oy_lst=self.app_overhead_lst,
                                        path=path)

    def create_fsb_fc_create_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова CREATE(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='create',
                                    path=path)

    def create_fsb_fc_write_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова WRITE(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='write',
                                    path=path)

    def create_fsb_fc_fsync_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова FSYNC(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='fsync',
                                    path=path)

    def create_fsb_fc_sync_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова SYNC(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='sync',
                                    path=path)

    def create_fsb_fc_close_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова CLOSE(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='close',
                                    path=path)

    def create_fsb_fc_unlink_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова UNLINK(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='unlink',
                                    path=path)

    def create_fsb_sz_create_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова CREATE(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='create',
                                    path=path)

    def create_fsb_sz_write_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова WRITE(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='write',
                                    path=path)

    def create_fsb_sz_fsync_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова FSYNC(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='fsync',
                                    path=path)

    def create_fsb_sz_sync_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова SYNC(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='sync',
                                    path=path)

    def create_fsb_sz_close_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова CLOSE(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='close',
                                    path=path)

    def create_fsb_sz_unlink_graph(self, path=REPORT_PATH):
        '''
            График зависимости скорости сис. вызова UNLINK(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='unlink',
                                    path=path)

    ####################################################################################################################
    def get_speed_rating(self,
                         x_lst,
                         accuracy=3,
                         multiplier=10**(0),
                         auto_normalize=True):

        if auto_normalize:
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array([0]+ self.speed_lst + [96000])[:, np.newaxis]) #max vector 4600 changed to 96000
            # print('normalized_data_2d_array --', scaler.fit_transform(np.array(self.speed_lst)[:, np.newaxis]))
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]
            # print('normalized_data_list --', [float(list(item)[0]) for item in list(normalized_data_2d_array)])

            func_speed = self.data_aproximation(x_lst, normalized_data_list)
            # print('func_speed --', self.data_aproximation(x_lst, normalized_data_list))
            i_spd, err = integrate.quad(func_speed, self.ox_lower_limit, self.ox_upper_limit-self.ox_step)
            # print('ox_lower_limit --', self.ox_lower_limit)
            # print('ox_upper_limit --', self.ox_upper_limit)
            # print('ox_step --', self.ox_step)
            # print('i_spd --', i_spd)
            # print('err --', err)
            if i_spd == 0:
                return 1
            else:
                print('*=====* get_speed_rating', round((i_spd * multiplier), accuracy))
                print('multiplier --', multiplier)
                print('accuracy --', accuracy)
                return round((i_spd * multiplier), accuracy)
        else:
            func_speed = self.data_aproximation(x_lst, self.speed_lst)
            print('func_speed --', self.data_aproximation(x_lst, self.speed_lst))
            i_spd, err = integrate.quad(func_speed, self.ox_lower_limit, self.ox_upper_limit)
            print('ox_lower_limit --', self.ox_lower_limit)
            print('ox_upper_limit --', self.ox_upper_limit)
            print('i_spd --', i_spd)
            print('err --', err)

            if i_spd == 0:
                return 1
            else:
                print('*=====* get_speed_rating', round(np.log(i_spd * multiplier), accuracy))
                print('multiplier --', multiplier)
                print('accuracy --', accuracy)
                return round(np.log(i_spd * multiplier), accuracy)

    def get_app_overhead_rating(self,
                                x_lst,
                                accuracy=3,
                                multiplier=10**(0),
                                auto_normalize=True): # -14

        if auto_normalize:
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array([0] + self.app_overhead_lst + [72600000])[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]

            func_ao = self.data_aproximation(x_lst, normalized_data_list)
            i_ao, err = integrate.quad(func_ao, self.ox_lower_limit, self.ox_upper_limit-self.ox_step)
            if i_ao == 0:
                return 1
            else:
                #print('*=====* get_app_overhead_rating', round((i_ao * multiplier), accuracy))
                return round((i_ao * multiplier), accuracy)
        else:
            func_ao = self.data_aproximation(x_lst, self.app_overhead_lst)
            i_ao, err = integrate.quad(func_ao, self.ox_lower_limit, self.ox_upper_limit)

            if i_ao == 0:
                return 1
            else:
                #print('*=====* get_app_overhead_rating', round(np.log(i_ao * multiplier), accuracy))
                return round(np.log(i_ao * multiplier), accuracy)

    def get_syscall_rating(self,
                           x_lst,
                           y_lst,
                           accuracy=3,
                           multiplier=10**(0),
                           auto_normalize=True): #-10

        if auto_normalize:
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(y_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]

            func = self.data_aproximation(x_lst, normalized_data_list)
            i, err = integrate.quad(func, self.ox_lower_limit, self.ox_upper_limit-self.ox_step)
            if i == 0:
                return 1
            else:
                #print('*=====* get_syscall_rating', round((i * multiplier), accuracy))
                return round((i * multiplier), accuracy)
        else:
            func = self.data_aproximation(x_lst, y_lst)
            i, err = integrate.quad(func, self.ox_lower_limit, self.ox_upper_limit)

            if i == 0:
                return 1
            else:
                #print('*=====* get_syscall_rating', round(np.log(i * multiplier), accuracy))
                return round(np.log(i * multiplier), accuracy)

    def get_total_rating(self,
                         x_lst,
                         accuracy=3,
                         multiplier=10**(0)): #-20

        # weight coefficients
        c_app_overhead_rating = 0.125
        c_create_rating = 0.5625
        c_write_rating = 0.5625
        c_fsync_rating = 0.5625
        c_sync_rating = 0.5625
        c_close_rating = 0.5625
        c_unlink_rating = 0.5625
        c_speed_rating = 1

        # print('*=====* get_total_rating', abs(round((c_speed_rating * self.get_speed_rating(x_lst)) + \
        #          (c_app_overhead_rating * self.get_app_overhead_rating(x_lst))**(-1) + \
        #          (c_create_rating * self.get_syscall_rating(x_lst, [0] + self.create_avg_lst + [1300]))**(-1) + \
        #          (c_write_rating * self.get_syscall_rating(x_lst, [0] + self.write_avg_lst + [270]))**(-1) + \
        #          (c_fsync_rating * self.get_syscall_rating(x_lst, [0] + self.fsync_avg_lst + [98000]))**(-1) + \
        #          (c_sync_rating * self.get_syscall_rating(x_lst, [0] + self.sync_avg_lst + [10]))**(-1) + \
        #          (c_close_rating * self.get_syscall_rating(x_lst, [0] + self.close_avg_lst + [100]))**(-1) + \
        #          (c_unlink_rating * self.get_syscall_rating(x_lst, [0] + self.unlink_avg_lst + [100]))**(-1) + \
        #          multiplier,
        #          accuracy)))

        return abs(round((c_speed_rating * self.get_speed_rating(x_lst)) + \
                 (c_app_overhead_rating * self.get_app_overhead_rating(x_lst))**(-1) + \
                 (c_create_rating * self.get_syscall_rating(x_lst, [0] + self.create_avg_lst + [1300]))**(-1) + \
                 (c_write_rating * self.get_syscall_rating(x_lst, [0] + self.write_avg_lst + [270]))**(-1) + \
                 (c_fsync_rating * self.get_syscall_rating(x_lst, [0] + self.fsync_avg_lst + [98000]))**(-1) + \
                 (c_sync_rating * self.get_syscall_rating(x_lst, [0] + self.sync_avg_lst + [10]))**(-1) + \
                 (c_close_rating * self.get_syscall_rating(x_lst, [0] + self.close_avg_lst + [100]))**(-1) + \
                 (c_unlink_rating * self.get_syscall_rating(x_lst, [0] + self.unlink_avg_lst + [100]))**(-1) + \
                 multiplier,
                 accuracy))

    ####################################################################################################################
    def merge(self, ox_lst, table_lst, graph_lst, path=REPORT_PATH):
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
            '    <title>FS_benchmark report</title>\n',
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
            '    <h1>FS. Stress testing</h1>\n',
            '    </div>\n',
            '    <div style="width:50%; height:1px; clear:both;"></div>\n',
            '    <div class="line_block">\n',
        ]

        html_template_part2 = [
            '    </div>\n',
            '    <div class="line_block">Total rating: {} penguins</div>\n'.format(self.get_total_rating(ox_lst)),
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
    def create_tar(path_to_files=REPORT_PATH):
        '''
            tar архив с результатами тестирования
        '''
        copy(LOG_PATH, '{}/main_log'.format(path_to_files))
        with tarfile.open('report{v}_{m}_{t}.tar'.format(v=astra_version()[0],
                                                         m=astra_version()[1],
                                                         t=time()), 'w') as tar:
            os.chdir(SCRIPT_DIR)
            for file in listdir(path_to_files):
                tar.add('{}/{}'.format('report', file))