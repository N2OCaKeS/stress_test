import os
import numpy as np
import pandas as pd
import warnings
#from numpy.exceptions import RankWarning
from os import path
from scipy import integrate
from matplotlib import pyplot as plt
from pretty_html_table import build_table
from sklearn import preprocessing
from ipa_conf import SCRIPT_DIR, REPORT_PATH


class Report:    
    def __init__(self, report_path=REPORT_PATH, img_width=16.256, img_height=12.192, file_name="ipa_report_norm.txt"):
        self.report_path = report_path
        self.width = img_width
        self.height = img_height
        self.file_name = file_name
        # create dir
        if not path.exists(REPORT_PATH):
            os.mkdir(REPORT_PATH, mode=0o755)

        with open(f"{REPORT_PATH}/{self.file_name}") as file:
            raw_data = file.read().split()

        self.user_count = [int(param) for param in raw_data[::6]]
        self.proc_errors = [float(param) for param in raw_data[1::6]]
        self.sr_znach = [float(param) for param in raw_data[2::6]]
        self.value_for_last_proc_delay = [float(param) for param in raw_data[3::6]]
        self.min_znach = [float(param) for param in raw_data[4::6]]
        self.max_znach = [float(param) for param in raw_data[5::6]]
        
        # self.id = [int(param) for param in raw_data[0::3]]
        # self.delay_for_all = [float(param) for param in raw_data[1::3]]
        # self.failures = [int(param) for param in raw_data[2::3]]
        # self.raw_table = pd.DataFrame({'id': self.id,
        #                                'delay_for_all': self.delay_for_all,
        #                                'failures': self.failures})

        self.raw_table = pd.DataFrame({'user_count': self.user_count,
                                       'proc_errors': self.proc_errors,
                                       'sr_znach': self.sr_znach,
                                       'value_for_last_proc_delay': self.value_for_last_proc_delay,
                                       'min_znach': self.min_znach,
                                       'max_znach': self.max_znach})

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
    

    def get_rating_proc_errors(self,
                               multiplier=10**(0),
                               accuracy=3,
                               auto_normalize=True):
        
        ox_lst = self.raw_table['user_count'].values.tolist()
        oy_lst = self.raw_table['proc_errors'].values.tolist()
        if auto_normalize:
            oy_lst = [0] + oy_lst + [100]
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(oy_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]
            if len(set(normalized_data_list)) == 1 and int(next(iter(set(normalized_data_list)))) == 0:
                # print("Залетаем сюда")
                normalized_data_list = [1.0 for _ in list(normalized_data_2d_array[1:-1])]
                # print(normalized_data_list)
            # func_proc_erros = self.data_aproximation(ox_lst, normalized_data_list)
            i_proc_errors, err = integrate.quad(func_proc_erros, 
                                               ox_lst[0], 
                                               ox_lst[-1])
            i_proc_errors, err = integrate.simpson(y=normalized_data_list, x=ox_lst)
            
            # print("RATING PROC ERR",i_proc_errors * multiplier)
            return i_proc_errors * multiplier
        else:
            func_proc_erros =self.data_aproximation(ox_lst, oy_lst)
            i_proc_errors, err = integrate.quad(func_proc_erros, 
                                               ox_lst[0], 
                                               ox_lst[-1])
            try:
                return round(i_proc_errors * multiplier, accuracy)
            except ZeroDivisionError:
                return 0



    def get_rating_sr_znach(self,
                            multiplier=10**(0),
                            accuracy=3,
                            auto_normalize=True):
        ox_lst = self.raw_table['user_count'].values.tolist()
        oy_lst = self.raw_table['sr_znach'].values.tolist()
        if auto_normalize:
            oy_lst = [0] + oy_lst + [1000]
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(oy_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]
            # print(normalized_data_list)
            if len(set(normalized_data_list)) == 1:
                normalized_data_list = [1.0 for _ in list(normalized_data_2d_array[1:-1])]
            func_sr_znach = self.data_aproximation(ox_lst, normalized_data_list)
            i_sr_znach, err = integrate.quad(func_sr_znach,
                                             ox_lst[0], 
                                             ox_lst[-1])
            # print(i_sr_znach * multiplier)
            return i_sr_znach * multiplier
        else:
            func_sr_znach = self.data_aproximation(ox_lst, oy_lst)
            i_sr_znach, err = integrate.quad(func_sr_znach,
                                             ox_lst[0],
                                             ox_lst[-1])
            try:
                return round(i_sr_znach * multiplier, accuracy)
            except ZeroDivisionError:
                return 0


    def get_rating_last_value(self,
                              multiplier=10**(0),
                              accuracy=3,
                              auto_normalize=True):
        ox_lst = self.raw_table['user_count'].values.tolist()
        oy_lst = self.raw_table['value_for_last_proc_delay'].values.tolist()
        if auto_normalize:
            oy_lst = [0] + oy_lst + [1000]
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(oy_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]
            if len(set(normalized_data_list)) == 1:
                normalized_data_list = [0.1 for _ in list(normalized_data_2d_array[1:-1])]
            func_last_value = self.data_aproximation(ox_lst, normalized_data_list)
            i_last_value, err = integrate.quad(func_last_value,
                                               ox_lst[0],
                                               ox_lst[-1])
            # print(i_last_value * multiplier)
            return i_last_value * multiplier
        else:
            func_last_value = self.data_aproximation(ox_lst, oy_lst)
            i_last_value, err = integrate.quad(func_last_value,
                                               ox_lst[0],
                                               ox_lst[-1])
            try:
                round(i_last_value * multiplier, accuracy)
            except ZeroDivisionError:
                return 0

    def get_total_rating(self,
                         multiplier=10**(2),
                         accuracy=10):
        cw_proc_errors = 0.1
        cw_sr_znach = 0.8
        cw_last_value = 0.1
        # try:
        print((self.get_rating_proc_errors())**(-1))
        print("_________________")
        print((self.get_rating_sr_znach())**(-1))
        print("_________________")
        # print((self.get_rating_last_value())**(-1))
        # print("_________________")
        total_freeipa_rating = round(
            ((cw_proc_errors * self.get_rating_proc_errors())**(-1) + (cw_sr_znach * self.get_rating_sr_znach())**(-1)) * multiplier,
            accuracy 
        )
        # except Exception as err:
        #     pass
        return total_freeipa_rating