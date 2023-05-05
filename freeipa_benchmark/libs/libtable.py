import os
import numpy as np
import pandas as pd
import warnings

from os import path
from scipy import integrate
from matplotlib import pyplot as plt
from pretty_html_table import build_table

from ipa_conf import SCRIPT_DIR, REPORT_PATH


class Report:    
    def __init__(self, report_path=REPORT_PATH, img_width=16.256, img_height=12.192):
        self.report_path = report_path
        self.width = img_width
        self.height = img_height
        
        # create dir
        if not path.exists(REPORT_PATH):
            os.mkdir(REPORT_PATH, mode=0o755)

        with open(f"{SCRIPT_DIR}/enrollement_report.txt") as file:
            raw_data = file.read().split()
        
        self.id = [int(param) for param in raw_data[0::3]]
        self.delay_for_all = [float(param) for param in raw_data[1::3]]
        self.failures = [int(param) for param in raw_data[2::3]]
        self.raw_table = pd.DataFrame({'id': self.id,
                                       'delay_for_all': self.delay_for_all,
                                       'failures': self.failures})

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

    
    def create_graph(self, x, y):
        pass


    def get_rating(self, x, y):
        '''
            Получить рейтинг
        '''
        func = self.data_aproximation(x, y)
        I, err = integrate.quad(func, x[0], x[-1])
        try:
            return 1/I
        except ZeroDivisionError:
            return 0
        

    def get_total_rating():
        pass


    def create_beauty_table(self, path=REPORT_PATH, table_name='ipa_enrollement_report_table.html'):
        beauty_table = build_table(self.raw_table, 'blue_light')
        with open('{}/{}'.format(path, table_name), 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)