import re
import pandas
import warnings

from pretty_html_table import build_table
from lsb_conf import REPORT_DIR, REPORT_FILENAME
import numpy as np
from sklearn import preprocessing
from scipy import integrate
from matplotlib import pyplot as plt
from libs.liblsb import astra_version

RESULT_PARSING_REGEXP = {
    'dhry2reg': r'Dhrystone\s2\susing\sregister\svariables\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'whetstone-double': r'Double-Precision\sWhetstone\s*(\d*.\d)\sMWIPS\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'execl': r'Execl\sThroughput\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'fstime': r'File\sCopy\s1024\sbufsize\s2000\smaxblocks\s*(\d*.\d)\sKBps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'fsbuffer': r'File\sCopy\s256\sbufsize\s500\smaxblocks\s*(\d*.\d)\sKBps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'fsdisk': r'File\sCopy\s4096\sbufsize\s8000\smaxblocks\s*(\d*.\d)\sKBps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'pipe': r'Pipe\sThroughput\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'context1': r'Pipe-based\sContext\sSwitching\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'spawn': r'Process\sCreation\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'shell1': r'Shell\sScripts\s\(1\sconcurrent\)\s*(\d*.\d)\slpm\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'shell8': r'Shell\sScripts\s\(8\sconcurrent\)\s*(\d*.\d)\slpm\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'syscall': r'System\sCall\sOverhead\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
}

TEST_MEASURE = {
    'dhry2reg': 'lps',
    'whetstone-double': 'MWIPS',
    'execl': 'lps',
    'fstime': 'KBps',
    'fsbuffer': 'KBps',
    'fsdisk': 'KBps',
    'pipe': 'lps',
    'context1': 'lps',
    'spawn': 'lps',
    'shell1': 'lpm',
    'shell8': 'lpm',
    'syscall': 'lps',
}

TEST_NAMES = ('dhry2reg',
              'whetstone-double',
              'execl',
              'fstime',
              'fsbuffer',
              'fsdisk',
              'pipe',
              'context1',
              'spawn',
              'shell1',
              'shell8',
              'syscall')


class Report:
    def __init__(self,
                 ox_lo_lim,
                 ox_up_lim,
                 ox_step,
                 report_dir=REPORT_DIR):

        self.__report_dir = report_dir

        self.__ox_lower_limit = ox_lo_lim
        self.__ox_upper_limit = ox_up_lim
        self.__ox_step = ox_step
        self.__grid_factor = 2

        # graph size
        self.__width = 27
        self.__height = 15

        # читаем из файла
        with open("{}/{}".format(self.__report_dir, REPORT_FILENAME), 'r') as file:
            text = file.read()

            # объявляем новый дикт
            self.__raw_dict = {}
            for test, regexp in RESULT_PARSING_REGEXP.items():  # ищем совпаденяи по тестам

                # чтоб не искать по десять раз, объявлю
                result_tuples = re.findall(regexp, text)
                # объявляем новый дикт с кючами значениями
                self.__raw_dict[test] = {}
                self.__raw_dict[test]['parallel_threads'] = [float(index) for index in range(self.__ox_lower_limit, self.__ox_upper_limit, self.__ox_step)]
                self.__raw_dict[test]['value'] = [float(result_tuple[0]) for result_tuple in result_tuples]
                self.__raw_dict[test]['time'] = [float(result_tuple[1]) for result_tuple in result_tuples]
                self.__raw_dict[test]['samples'] = [float(result_tuple[2]) for result_tuple in result_tuples]

            print(self.__raw_dict)

        # соберем датафреймы
        self.__raw_tables = {}
        for test in TEST_NAMES:
            self.__raw_tables[test] = pandas.DataFrame(self.__raw_dict[test])
            print(self.__raw_tables[test])

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

    def create_beauty_table(self,
                            test,
                            table_name='lsb_{t}_table.html'):
        '''
        :param test: имя теста
        :param path: директория с файлами отчета
        :param table_name: имя файла html для сохранения таблицы
        :return:
        '''
        beauty_table = build_table(self.__raw_tables[test], 'blue_light')
        with open('{}/{}'.format(self.__report_dir, table_name.format(t=test)), 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)

    def create_beauty_tables(self,
                             test_list=TEST_NAMES,
                             table_name='lsb_{t}_table.html'):
        '''
        :param test: список имен тестов
        :param path: директория с файлами отчета
        :param table_name: имя файла html для сохранения таблицы
        :return:
        '''
        for test in test_list:
            self.create_beauty_table(test, table_name)

    def template_aproximated_graph(self,
                                   test,
                                   ox_param_table_name,
                                   oy_param_table_name,
                                   measures=TEST_MEASURE):
        '''
            Шаблон графика с апроксимацией и точками
        '''
        # points
        x = self.__raw_tables[test].loc[:, [ox_param_table_name]]
        y = self.__raw_tables[test].loc[:, [oy_param_table_name]]

        ox_lst = self.__raw_dict[test][ox_param_table_name]
        oy_lst = self.__raw_dict[test][oy_param_table_name]

        # build function f(x)
        aprx_x = np.arange(self.__ox_lower_limit, self.__ox_upper_limit-self.__ox_step, 0.1)
        aprx_f = self.data_aproximation(ox_lst, oy_lst)

        # build graph
        plt.figure(figsize=(self.cm_to_inch(self.__width), self.cm_to_inch(self.__height)))
        plt.plot(x, y, 'o'),
        plt.plot(aprx_x, aprx_f(aprx_x))
        plt.title('{digit_varsion}({mode}). {ytitle}/{xtitle}'.format(digit_varsion=astra_version()[0],
                                                                      mode=astra_version()[1],
                                                                      xtitle=ox_param_table_name,
                                                                      ytitle=oy_param_table_name))
        plt.xlabel(ox_param_table_name)
        ox_ticks = np.arange(self.__ox_lower_limit,
                             self.__ox_upper_limit,
                             self.__grid_factor)
        plt.xticks(ox_ticks, ox_ticks, rotation='vertical')
        plt.ylabel('{}({})'.format(oy_param_table_name, measures[test]))
        plt.grid(True)

        plt.savefig('{p}/fsb_{ox}_{oy}_graph'.format(p=self.__report_dir,
                                                     ox=ox_param_table_name,
                                                     oy=oy_param_table_name))

    def create_dhry2reg_graph(self):
        self.template_aproximated_graph(test='dhry2reg',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_whetstone_double_graph(self):
        self.template_aproximated_graph(test='whetstone-double',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_execl_graph(self):
        self.template_aproximated_graph(test='execl',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_fstime_graph(self):
        self.template_aproximated_graph(test='fstime',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_fsbuffer_graph(self):
        self.template_aproximated_graph(test='fsbuffer',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_fsdisk_graph(self):
        self.template_aproximated_graph(test='fsdisk',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_pipe_graph(self):
        self.template_aproximated_graph(test='pipe',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_context1_graph(self):
        self.template_aproximated_graph(test='context1',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_spawn_graph(self):
        self.template_aproximated_graph(test='spawn',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_shell1_graph(self):
        self.template_aproximated_graph(test='shell1',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_shell8_graph(self):
        self.template_aproximated_graph(test='shell8',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def create_syscall_graph(self):
        self.template_aproximated_graph(test='syscall',
                                        ox_param_table_name='parallel_threads',
                                        oy_param_table_name='value',
                                        measures=TEST_MEASURE)

    def get_rating(self,
                   x_lst,
                   accuracy=3,
                   multiplier=10**(0),
                   auto_normalize=True):

        if auto_normalize:
            scaler = preprocessing.MinMaxScaler()
            normalized_data_2d_array = scaler.fit_transform(np.array(self.speed_lst)[:, np.newaxis])
            normalized_data_list = [float(list(item)[0]) for item in list(normalized_data_2d_array)]

            func_speed = self.data_aproximation(x_lst, normalized_data_list)
            i_spd, err = integrate.quad(func_speed, self.ox_lower_limit, self.ox_upper_limit-self.ox_step)
            if i_spd == 0:
                return 1
            else:
                return round((i_spd * multiplier), accuracy)
        else:
            pass

r = Report(2, 14, 2)
r.create_dhry2reg_graph()



