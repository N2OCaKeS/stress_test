import os
import warnings
import numpy as np
import pandas as pd
from os.path import exists
from scipy import integrate
from sklearn import preprocessing
from matplotlib import pyplot as plt
from numpy.exceptions import RankWarning
from pretty_html_table import build_table

from exb_conf import REPORT_FILENAME, REPORT_PATH

class Report:
    def __init__(self, report_path=REPORT_PATH, img_width=16.256, img_height=12.192):
        self.report_path = report_path
        self.width = img_width
        self.height = img_height
        
        if not exists(REPORT_PATH):
            os.mkdir(REPORT_PATH, mode=0o755)

        with open(f"{REPORT_FILENAME}") as file:
            raw_data = file.read().split()

        self.mail_count = [int(param) for param in raw_data[::3]]
        self.successful = [int(param) for param in raw_data[1::3]]
        self.emails_per_second = [int(param) for param in raw_data[2::3]]

        self.raw_table = pd.DataFrame({'mail_count': self.mail_count,
                                        'successful': self.successful,
                                        'emails_per_second': self.emails_per_second})
        
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

        func = self.data_aproximation(x, normalized_data_list)
        I, err = integrate.quad(func, x[0], x[-1])
        return I
    
    def get_rating_successful(self):
        return self.get_rating(x=self.raw_table['mail_count'].tolist(), 
                               y=self.raw_table['successful'].tolist(),
                               y_min_for_mathmodel=0,
                               y_max_for_mathmodel=...)
    
    def get_rating_emails_per_second(self):
        return self.get_rating(x=self.raw_table['mail_count'].tolist(), 
                               y=self.raw_table['emails_per_second'].tolist(),
                               y_min_for_mathmodel=0,
                               y_max_for_mathmodel=...)
    
    def get_total_rating(self):
        pass

        
