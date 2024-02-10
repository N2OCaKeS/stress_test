import os
import numpy as np
import pandas as pd
import warnings

from os import path
from scipy import integrate
from matplotlib import pyplot as plt
from pretty_html_table import build_table
from sklearn import preprocessing
from ipa_conf import SCRIPT_DIR, REPORT_PATH


class Report:    
    def __init__(self, report_path=REPORT_PATH, img_width=16.256, img_height=12.192):
        self.report_path = report_path
        self.width = img_width
        self.height = img_height
        
        # create dir
        if not path.exists(REPORT_PATH):
            os.mkdir(REPORT_PATH, mode=0o755)

        with open(f"{REPORT_PATH}/ipa_report.txt") as file:
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
        '''
            Получить рейтинг
        '''
        
        y_new = [y_min_for_mathmodel] + y + [y_max_for_mathmodel]
        scaler = preprocessing.MinMaxScaler()
        normalized_data_2d_array = scaler.fit_transform(np.array(y_new)[:, np.newaxis])
        normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array[1:-1])]

        func = self.data_aproximation(x, normalized_data_list)
        I, err = integrate.quad(func, x[0], x[-1])
        try:
            return 1/I
        except ZeroDivisionError:
            return 0
        

    def get_total_rating(self, list_rating):
        c_weiht = 0.333
        total_rating = 1
        for item_rating in list_rating:
            # print(item_rating)
            total_rating += c_weiht * item_rating
        return round(total_rating * 1000, 2) #временное решение


    def create_beauty_table(self, path=REPORT_PATH, table_name='ipa_auth_report_table.html'):
        beauty_table = build_table(self.raw_table, 'blue_light')
        with open('{}/{}'.format(path, table_name), 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)