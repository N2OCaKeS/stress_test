import numpy as np
import pandas as pd
import warnings
from scipy import integrate
from sklearn import preprocessing
from numpy.exceptions import RankWarning

class Report:
    def __init__(self, dataframe) -> None:
        self.dataframe = dataframe

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
        return self.get_rating(x=self.dataframe['user_count'].tolist(), 
                               y=self.dataframe['sr_znach'].tolist(),
                               y_min_for_mathmodel=0,
                               y_max_for_mathmodel=650)

    def get_rating_value_for_last_proc_delay(self):
        return self.get_rating(x=self.dataframe['user_count'].tolist(),
                               y=self.dataframe['value_for_last_proc_delay'].tolist(),
                               y_min_for_mathmodel=0,
                               y_max_for_mathmodel=650)

    def get_rating_proc_errors(self, weight_c):
        proc_errors = self.dataframe['proc_errors']
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