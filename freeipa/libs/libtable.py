import os
import numpy as np
import pandas as pd
import warnings
from numpy.exceptions import RankWarning
from os import path
from scipy import integrate
from matplotlib import pyplot as plt
from pretty_html_table import build_table
from sklearn import preprocessing
from ipa_conf import SCRIPT_DIR, REPORT_PATH


class Report:    
    def __init__(self, report_path=REPORT_PATH, img_width=16.256, img_height=12.192, type_test="auth"):
        self.report_path = report_path
        self.width = img_width
        self.height = img_height
        self.type_test = type_test
        
        # create dir
        if not path.exists(REPORT_PATH):
            os.mkdir(REPORT_PATH, mode=0o755)

        with open(f"{REPORT_PATH}/ipa_report.txt") as file:
            raw_data = file.read().split()

        if self.type_test == "auth":
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
        elif self.type_test == "create_user":
            self.user_count = [int(param) for param in raw_data[::4]]
            self.successful_users = [int(param) for param in raw_data[1::4]]
            self.total_time = [int(param) for param in raw_data[2::4]]
            self.average_time_per_user = [int(param) for param in raw_data[3::4]]


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

    
    def create_graph(self, x, y, filename, title_graph, x_label, y_label):
        fig, ax = plt.subplots(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        ax.plot(x, y)
        ax.set_yscale("linear")
        # ax.set_xlim(0, x_rlim)
        ax.set_title(title_graph)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.grid(True)
        fig.savefig(f'{REPORT_PATH}/{filename}.png')


    def get_rating(self, x, y, y_min_for_mathmodel, y_max_for_mathmodel):
        y_new = [y_min_for_mathmodel] + y + [y_max_for_mathmodel]
        scaler = preprocessing.MinMaxScaler()
        normalized_data_2d_array = scaler.fit_transform(np.array(y_new)[:, np.newaxis])
        normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]
        # print(normalized_data_list)

        func = self.data_aproximation(x, normalized_data_list)
        I, err = integrate.quad(func, x[0], x[-1])
        return I
        
    def get_rating_sr_znach(self):
        return self.get_rating(x=self.raw_table['user_count'].tolist(), 
                               y=self.raw_table['sr_znach'].tolist(),
                               y_min_for_mathmodel=0,
                               y_max_for_mathmodel=650)

    def get_rating_value_for_last_proc_delay(self):
        return self.get_rating(x=self.raw_table['user_count'].tolist(),
                               y=self.raw_table['value_for_last_proc_delay'].tolist(),
                               y_min_for_mathmodel=0,
                               y_max_for_mathmodel=650)
    
    def get_rating_successful_users(self):
        return self.get_rating(x=self.raw_table['user_count'].tolist(),
                               y=self.raw_table['successful_users'].tolist(),
                               y_min_for_mathmodel=0,
                               y_max_for_mathmodel=...)
    
    def get_rating_total_time(self):
        return self.get_rating(x=self.raw_table['user_count'].tolist(),
                               y=self.raw_table['total_time'].tolist(),
                               y_min_for_mathmodel=0,
                               y_max_for_mathmodel=...)
    
    def get_rating_average_time_per_user(self):
        return self.get_rating(x=self.raw_table['user_count'].tolist(),
                               y=self.raw_table['average_time_per_user'].tolist(),
                               y_min_for_mathmodel=0,
                               y_max_for_mathmodel=...)


    def get_rating_proc_errors(self, weight_c):
        proc_errors = self.raw_table['proc_errors']
        max_proc_err = proc_errors.max()
        if max_proc_err == 0:
            return 1
        elif max_proc_err > 0 and max_proc_err < 10:
            return 0.9
        elif max_proc_err > 10:
            return 1 - weight_c

    def get_total_rating(self,
                         multiplier=10**(4),
                         accuracy=2):
        weight_c_sr_znach = 0.5 # 0.5
        weight_c_value_for_last_proc_delay = 0.2 # 0.2
        weight_c_proc_errors = 0.3
        total_rating = (
            (
                ((self.get_rating_sr_znach() * weight_c_sr_znach)**(-1)) + 
                ((self.get_rating_value_for_last_proc_delay() * weight_c_value_for_last_proc_delay)**(-1))
            ) * self.get_rating_proc_errors(weight_c_proc_errors)
        )
        return round(total_rating * multiplier, accuracy)
    
    def get_total_rating_create_users_test(self, 
                                           multiplier=10**(4),
                                           accuracy=2):
        weight_successful_users = ...
        weight_total_time = ...
        weight_average_time_per_user = ...
        total_rating = (
            ((self.get_rating_successful_users() * weight_successful_users) ** (1)) +
            ((self.get_rating_total_time() * weight_total_time) ** (-1)) +
            ((self.get_rating_average_time_per_user() * weight_average_time_per_user) ** (-1))
        )
        return round(total_rating * multiplier, accuracy)


    def create_beauty_table(self, path=REPORT_PATH, table_name='ipa_auth_report_table.html'):
        beauty_table = build_table(self.raw_table, 'blue_light')
        with open('{}/{}'.format(path, table_name), 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)