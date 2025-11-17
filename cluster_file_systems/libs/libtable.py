# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import json
import pandas
import tarfile
import warnings
import numpy as np
# from numpy.exceptions import RankWarning

from shutil import copy
from time import time
from os import listdir, chdir
from scipy import integrate
from sklearn import preprocessing
from matplotlib import pyplot as plt
from libs.libcfs import astra_version
from pretty_html_table import build_table
from cfs_conf import REPORT_DIR, REPORT_FILENAME, LOG_PATH, LOG_FILENAME, SCRIPT_DIR, \
    START_BORDER_FOR_DATA, STEP_FOR_DATA, END_BORDER_FOR_DATA, \
    FILES_LIMIT, FILES_STEP, FILES, REPORT_DIR_HOST

try:
    from numpy.exceptions import RankWarning
except ImportError:
    from numpy import RankWarning


class ReportFIO:
    
    def __init__(self, file_path=f"{REPORT_DIR_HOST}/report_fio.txt"):
        self.file_path = file_path
        self.results = {
            'read': {'slat_avg': None, 'clat_avg': None, 'lat_avg': None, 'iops': None},
            'write': {'slat_avg': None, 'clat_avg': None, 'lat_avg': None, 'iops': None}
        }

    
    def parse_fio_json(self):
        with open(self.file_path, 'r') as file:
            data = json.load(file)

        job = data['jobs'][0]
        if 'read' in job:
            read_data = job['read']
            self.results['read']['slat_avg'] = round(read_data['slat_ns']['mean'] / 1000, 1)
            self.results['read']['clat_avg'] = round(read_data['clat_ns']['mean'] / 1000, 1)
            self.results['read']['lat_avg'] = round(read_data['lat_ns']['mean'] / 1000, 1)
            self.results['read']['iops'] = round(read_data['iops'], 0)

        if 'write' in job:
            write_data = job['write']
            self.results['write']['slat_avg'] = round(write_data['slat_ns']['mean'] / 1000, 1)
            self.results['write']['clat_avg'] = round(write_data['clat_ns']['mean'] / 1000, 1)
            self.results['write']['lat_avg'] = round(write_data['lat_ns']['mean'] / 1000, 1)
            self.results['write']['iops'] = round(write_data['iops'], 0)
        
        return self.results


    def parse_fio_file(self):
        current_section = None

        with open(self.file_path, 'r') as file:
            for line in file:
                line = line.strip()
                
                if line.startswith('read:'):
                    current_section = 'read'
                    if 'IOPS=' in line:
                        iops_part = line.split('IOPS=')[1].split(',')[0]
                        self.results[current_section]['iops'] = float(iops_part)
                elif line.startswith('write:'):
                    current_section = 'write'
                    if 'IOPS=' in line:
                        iops_part = line.split('IOPS=')[1].split(',')[0]
                        self.results[current_section]['iops'] = float(iops_part)
                
                if current_section:
                    if 'slat (' in line and 'avg=' in line:
                        parts = line.split('avg=')[1].split(',')[0]
                        self.results[current_section]['slat_avg'] = float(parts)

                    elif 'clat (' in line and 'avg=' in line and 'percentiles' not in line:
                        parts = line.split('avg=')[1].split(',')[0]
                        self.results[current_section]['clat_avg'] = float(parts)
                    
                    elif line.startswith('lat (') and 'avg=' in line:
                        parts = line.split('avg=')[1].split(',')[0]
                        self.results[current_section]['lat_avg'] = float(parts)

        return self.results


    def create_report(self):
        try:
            results = self.parse_fio_json()
            print(results)

            df = pandas.DataFrame(results).T
            print(df)
            df.to_html(f"{REPORT_DIR_HOST}/result_fio.html")
        
        except FileNotFoundError:
            print(f"файл '{self.file_path}' c результами не найден!")
        except Exception as e:
            print(f"произошла ошибка: {str(e)}")



class Report:
    def __init__(self,
                 ox_lo_lim,
                 ox_step,
                 ox_up_lim,
                 report=REPORT_DIR,
                 #report='{}/{}'.format(REPORT_DIR, REPORT_FILENAME),
                 mtreading=False):

        self.ox_lower_limit = ox_lo_lim
        self.ox_step = ox_step
        self.ox_upper_limit = ox_up_lim
        self.current_report_dir = report
        self.mtreading = mtreading

        '''
            :param report: path to report file
            read and parsing data from report file
        '''
        with open('{}/{}'.format(self.current_report_dir, REPORT_FILENAME), 'r') as report_file:
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
                                                'unlink_max': self.unlink_max_lst}).sort_values(by=['file_count', 'file_size'])

        if mtreading:
            self.summary_raw_table = self.raw_table.groupby(by='file_count').mean().reset_index()
            self.summary_file_count_lst = self.summary_raw_table['file_count'].values.tolist()
            self.summary_fs_use_lst = self.summary_raw_table['fs_use'].values.tolist()
            self.summary_speed_lst = self.summary_raw_table["speed"].values.tolist()
            self.summary_app_overhead_lst = self.summary_raw_table['app_overhead'].values.tolist()
            self.summary_create_avg_lst = self.summary_raw_table['create_avg'].values.tolist()
            self.summary_write_avg_lst = self.summary_raw_table['write_avg'].values.tolist()
            self.summary_fsync_avg_lst = self.summary_raw_table['fsync_avg'].values.tolist()
            self.summary_sync_avg_lst = self.summary_raw_table['sync_avg'].values.tolist()
            self.summary_close_avg_lst = self.summary_raw_table['close_avg'].values.tolist()
            self.summary_unlink_avg_lst = self.summary_raw_table['unlink_avg'].values.tolist()

            print(self.raw_table)
            print(self.summary_raw_table)

        # graph size
        self.width = 27
        self.height = 15

        self.grid_factor = self.ox_upper_limit // 10
        # self.grid_factor = FILES_STEP

    '''
        Создать html таблицу
    '''
    def create_beauty_table(self, table_name='cfs_report_table.html'):
        beauty_table = build_table(self.raw_table, 'blue_light')
        with open('{}/{}'.format(self.current_report_dir, table_name), 'w') as beauty_html_table:
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
                except RankWarning:
                    polinom_factor -= 1

    ####################################################################################################################
    def template_aproximated_graph(self,
                                   ox_param_table_name,
                                   ox_lst,
                                   oy_param_table_name,
                                   oy_lst):
        '''
            Шаблон графика с апроксимацией и точками
        '''
        # points
        x = self.raw_table.loc[:, [ox_param_table_name]]
        y = self.raw_table.loc[:, [oy_param_table_name]]

        # build function f(x)
        aprx_x = np.arange(self.ox_lower_limit, self.ox_upper_limit-self.ox_step, 1)
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
        # ox шкала
        ox_ticks = np.arange(self.ox_lower_limit,
                             self.ox_upper_limit,
                             self.grid_factor)
        plt.xticks(ox_ticks, ox_ticks, rotation='vertical')
        # oy шкала
        oy_upper_limit = max(y[oy_param_table_name].values.tolist())
        if self.mtreading:
            oy_ticks = np.arange(0, oy_upper_limit, oy_upper_limit // 10)
            plt.yticks(oy_ticks, oy_ticks)

        plt.ylabel('{}(msec)'.format(oy_param_table_name))
        plt.grid(True)

        plt.savefig('{p}/cfs_{ox}_{oy}_graph'.format(p=self.current_report_dir,
                                                     ox=ox_param_table_name,
                                                     oy=oy_param_table_name))

    def template_syscall_graph(self,
                               ox_param_table_name,
                               ox_lst,
                               syscall):
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
        plt.plot(x, y1, 'o-', color='g',)
        plt.plot(x, y2, 'o-',  color='y',)
        plt.plot(x, y3, 'o-',  color='r',)
        plt.title('{digit_varsion}({mode}). {title1}/{title2}'.format(digit_varsion=astra_version()[0],
                                                                      mode=astra_version()[1],
                                                                      title1=ox_param_table_name,
                                                                      title2=syscall.upper()))
        plt.legend(['{} min'.format(syscall.upper()),
                    '{} avg'.format(syscall.upper()),
                    '{} max'.format(syscall.upper())])
        plt.xlabel(ox_param_table_name)

        # ox шкала
        ox_ticks = np.arange(self.ox_lower_limit,
                             self.ox_upper_limit,
                             self.grid_factor)
        plt.xticks(ox_ticks, ox_ticks, rotation='vertical')
        # oy шкала
        oy_upper_limit = max(self.raw_table['{}_max'.format(syscall)].values.tolist())
        if self.mtreading:
            try:
                oy_ticks = np.arange(0, oy_upper_limit, oy_upper_limit // 10)
            except ZeroDivisionError:
                oy_ticks = np.arange(10)
            plt.yticks(oy_ticks, oy_ticks)

        plt.ylabel('syscall {}(msec)'.format(syscall.upper()))
        plt.grid(True)

        plt.savefig('{}/cfs_{}_{}_graph'.format(self.current_report_dir,
                                                ox_param_table_name,
                                                syscall))

    def create_cfs_fc_sp_graph(self):
        '''
            График зависимости скорости работы от количества файлов
        '''
        self.template_aproximated_graph(ox_param_table_name='file_count',
                                        ox_lst=self.file_count_lst,
                                        oy_param_table_name='speed',
                                        oy_lst=self.speed_lst)

    def create_cfs_fc_app_overhead_graph(self):
        '''
            График зависимости накладных расходов(мсек) от количества файлов.
            Без выполнения системных вызовов, связанных с записью файла.
        '''
        self.template_aproximated_graph(ox_param_table_name='file_count',
                                        ox_lst=self.file_count_lst,
                                        oy_param_table_name='app_overhead',
                                        oy_lst=self.app_overhead_lst)

    def create_cfs_sz_sp_graph(self):
        '''
            График зависимости скорости работы от размера файлов
        '''
        self.template_aproximated_graph(ox_param_table_name='file_size',
                                        ox_lst=self.file_size_lst,
                                        oy_param_table_name='speed',
                                        oy_lst=self.speed_lst)

    def create_cfs_sz_app_overhead_graph(self):
        '''
            График зависимости накладных расходов(мсек) от размера файлов.
            Без выполнения системных вызовов, связанных с записью файла.
        '''
        self.template_aproximated_graph(ox_param_table_name='file_size',
                                        ox_lst=self.file_size_lst,
                                        oy_param_table_name='app_overhead',
                                        oy_lst=self.app_overhead_lst)

    def create_cfs_fc_create_graph(self):
        '''
            График зависимости скорости сис. вызова CREATE(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='create')

    def create_cfs_fc_write_graph(self):
        '''
            График зависимости скорости сис. вызова WRITE(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='write')

    def create_cfs_fc_fsync_graph(self):
        '''
            График зависимости скорости сис. вызова FSYNC(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='fsync')

    def create_cfs_fc_sync_graph(self):
        '''
            График зависимости скорости сис. вызова SYNC(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='sync')

    def create_cfs_fc_close_graph(self):
        '''
            График зависимости скорости сис. вызова CLOSE(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='close')

    def create_cfs_fc_unlink_graph(self):
        '''
            График зависимости скорости сис. вызова UNLINK(мсек) от количества файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_count',
                                    ox_lst=self.file_count_lst,
                                    syscall='unlink')

    def create_cfs_sz_create_graph(self):
        '''
            График зависимости скорости сис. вызова CREATE(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='create')

    def create_cfs_sz_write_graph(self):
        '''
            График зависимости скорости сис. вызова WRITE(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='write')

    def create_cfs_sz_fsync_graph(self):
        '''
            График зависимости скорости сис. вызова FSYNC(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='fsync')

    def create_cfs_sz_sync_graph(self):
        '''
            График зависимости скорости сис. вызова SYNC(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='sync')

    def create_cfs_sz_close_graph(self):
        '''
            График зависимости скорости сис. вызова CLOSE(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='close')

    def create_cfs_sz_unlink_graph(self):
        '''
            График зависимости скорости сис. вызова UNLINK(мсек) от размера файлов.
        '''
        self.template_syscall_graph(ox_param_table_name='file_size',
                                    ox_lst=self.file_size_lst,
                                    syscall='unlink')

    ####################################################################################################################
    def get_speed_rating(self,
                         x_lst,
                         y_lst,
                         accuracy=3,
                         multiplier=10**(0),
                         auto_normalize=False):

        if auto_normalize:
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(y_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array)]

            func_speed = self.data_aproximation(x_lst, normalized_data_list)
            i_spd, err = integrate.quad(func_speed, self.ox_lower_limit, self.ox_upper_limit-self.ox_step)
            if i_spd == 0:
                return 1
            else:
                return round((i_spd * multiplier), accuracy)
        else:
            func_speed = self.data_aproximation(x_lst, self.speed_lst)
            i_spd, err = integrate.quad(func_speed, self.ox_lower_limit, self.ox_upper_limit)
            if i_spd == 0:
                return 1
            else:
                return round(np.log(i_spd * multiplier), accuracy)

    def get_app_overhead_rating(self,
                                x_lst,
                                y_lst,
                                accuracy=3,
                                multiplier=10**(0),
                                auto_normalize=False): # -14

        if auto_normalize:
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(y_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array)]

            func_ao = self.data_aproximation(x_lst, normalized_data_list)
            i_ao, err = integrate.quad(func_ao, self.ox_lower_limit, self.ox_upper_limit-self.ox_step)
            if i_ao == 0:
                return 1
            else:
                return round((i_ao * multiplier), accuracy)
        else:
            func_ao = self.data_aproximation(x_lst, self.app_overhead_lst)
            i_ao, err = integrate.quad(func_ao, self.ox_lower_limit, self.ox_upper_limit)
            if i_ao == 0:
                return 1
            else:
                return round(np.log(i_ao * multiplier), accuracy)

    def get_syscall_rating(self,
                           x_lst,
                           y_lst,
                           accuracy=3,
                           multiplier=10**(0),
                           auto_normalize=False): #-10

        if auto_normalize:
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(y_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array)]

            func = self.data_aproximation(x_lst, normalized_data_list)
            i, err = integrate.quad(func, self.ox_lower_limit, self.ox_upper_limit-self.ox_step)
            if i == 0:
                return 1
            else:
                return round((i * multiplier), accuracy)
        else:
            func = self.data_aproximation(x_lst, y_lst)
            i, err = integrate.quad(func, self.ox_lower_limit, self.ox_upper_limit)
            if i == 0:
                return 1
            else:
                return round(np.log(i * multiplier), accuracy)

    def get_total_rating(self,
                         x_lst,
                         accuracy=3,
                         multiplier=10**(6)): #6

        # weight coefficients
        c_app_overhead_rating = 0.125
        c_create_rating = 0.5625
        c_write_rating = 0.5625
        c_fsync_rating = 0.5625
        c_sync_rating = 0.5625
        c_close_rating = 0.5625
        c_unlink_rating = 0.5625
        c_speed_rating = 1

        if self.mtreading:
            return abs(round((c_speed_rating * self.get_speed_rating(x_lst, self.summary_speed_lst)) * \
                             (c_app_overhead_rating * self.get_app_overhead_rating(x_lst, self.summary_app_overhead_lst))**(-1) * \
                             (c_create_rating * self.get_syscall_rating(x_lst, self.summary_create_avg_lst))**(-1) * \
                             (c_write_rating * self.get_syscall_rating(x_lst, self.summary_write_avg_lst))**(-1) * \
                             (c_fsync_rating * self.get_syscall_rating(x_lst, self.summary_fsync_avg_lst))**(-1) * \
                             (c_sync_rating * self.get_syscall_rating(x_lst, self.summary_sync_avg_lst))**(-1) * \
                             (c_close_rating * self.get_syscall_rating(x_lst, self.summary_close_avg_lst))**(-1) * \
                             (c_unlink_rating * self.get_syscall_rating(x_lst, self.summary_unlink_avg_lst))**(-1) * \
                             multiplier,
                             accuracy))
        else:
            return round((c_speed_rating * self.get_speed_rating(x_lst, self.speed_lst)) * \
                         (c_app_overhead_rating * self.get_app_overhead_rating(x_lst, self.app_overhead_lst))**(-1) * \
                         (c_create_rating * self.get_syscall_rating(x_lst, self.create_avg_lst))**(-1) * \
                         (c_write_rating * self.get_syscall_rating(x_lst, self.write_avg_lst))**(-1) * \
                         (c_fsync_rating * self.get_syscall_rating(x_lst, self.fsync_avg_lst))**(-1) * \
                         (c_sync_rating * self.get_syscall_rating(x_lst, self.sync_avg_lst))**(-1) * \
                         (c_close_rating * self.get_syscall_rating(x_lst, self.close_avg_lst))**(-1) * \
                         (c_unlink_rating * self.get_syscall_rating(x_lst, self.unlink_avg_lst))**(-1) * \
                         multiplier,
                         accuracy)

    ####################################################################################################################
    def merge(self, ox_lst, table_lst, graph_lst):
        '''
            Создать HTML
        '''

        tables_in_total_html = []
        for table in table_lst:
            with open('{}/{}'.format(self.current_report_dir, table)) as file:
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

        with open('{}/main_report.html'.format(self.current_report_dir), 'w') as total_html:
            total_html.writelines(html_template_part1)
            total_html.writelines(tables_in_total_html)
            total_html.writelines(html_template_part2)
            total_html.writelines(graphs_in_total_html)
            total_html.writelines(html_template_part3)

    def create_tar(self, path_to_tar=SCRIPT_DIR):
        '''
            tar архив с результатами тестирования
        '''

        with tarfile.open('{p}/report{v}_{m}_{t}.tar'.format(p=path_to_tar,
                                                             v=astra_version()[0],
                                                             m=astra_version()[1],
                                                             t=time()), 'w') as tar:
            chdir(path_to_tar)
            for file in listdir(self.current_report_dir):
                tar.add('{}/{}'.format('report', file))