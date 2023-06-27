# -*- coding: utf-8 -*-

# ;===========================================================
# ; Author: ivelikanov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import tarfile
import warnings
import numpy as np
import pandas as pd

from scipy import integrate
from os import listdir, chdir
from datetime import datetime
from matplotlib import pyplot as plt
from libs.libsng import astra_version


class Report:

    def __init__(self, report_path, img_width=16.256, img_height=12.192):
        self.report_path = report_path

        # graph size
        # default value 640x480
        # img_width=16.256
        # img_height=12.192

        self.width = img_width
        self.height = img_height


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


    def create_graph(self, x, y, filename, title_graph, x_rlim, x_label="Tsec", y_label=""):
        '''
            Построить граф
        '''
        aprx_x = np.arange(x[0], x[-1], 0.1)
        aprx_f = self.data_aproximation(x, y)

        # build graph
        fig, ax = plt.subplots(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        ax.plot(x, y, aprx_x, aprx_f(aprx_x))
        ax.set_yscale("linear")
        ax.set_xlim(0, x_rlim)
        ax.set_title(title_graph)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.grid(True)
        fig.savefig('{path}/{file_name}'.format(path=self.report_path, file_name=filename))
        return "{file_name}.png".format(path=self.report_path, file_name=filename)
        

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


    # def get_total_rating(self, list_rating=[], accuracy=10):
    #     '''
    #         Получить общий рейтинг
    #     '''
    #     total_rating = 0
    #     if len(list_rating) is 0:
    #         print("\033[31mНе передан список рейтингов\033[0m")
    #         raise ValueError
    #     for item_rating in list_rating:
    #         total_rating += item_rating
    #     return round(round(total_rating, accuracy) * 1000000, 3)

    def get_total_rating(self, list_rating=[]):
        c_weiht = 0.25
        # Итоговый рейтинг
        # total_rating = round((c_weiht * list_rating[0]) * (c_weiht * list_rating[1]) * (c_weiht * list_rating[2]) * (c_weiht * list_rating[3]) * 10**20, 3)
        total_rating = 1
        for item_rating in list_rating:
            total_rating += c_weiht * item_rating
        return round(total_rating * 10**5, 3)

    def create_html(self, graph_lst, total_rating, service_count, time_execution):    
        '''
            Создать HTML
        '''
        graphs_in_total_html = []
        for graph in graph_lst:
            graphs_in_total_html.append('<div class="graph_block"><img src="{}"></div>\n'.format(graph))

        html_template_part1 = [
            '<!DOCTYPE html>\n',
            '<html>\n',
            '  <head>\n',
            '    <meta charset="utf-8">\n',
            '    <title>Syslog-NG report</title>\n',
            '    <link href="./style.css" rel="stylesheet" type="text/css">\n',
            '  </head>\n',
            '  <body>\n',
            '    <style>\n',
            '        .line_block {\n',
            '                width:48%;\n',
            '                min-width: {}px;\n'.format(self.cm_to_inch(self.width) * 100 + 50),
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
            '    <div style = "width:99%; background:#f1f1f1; float:left; margin: 0 15px 15px 0; padding: 10px;">\n',
            '    <h1>Stress testing Syslog-NG</h1>\n',
            '    </div>\n',
            '    <div style="width:50%; height:1px; clear:both;"></div>\n',
            # '    <div class="line_block">\n',
        ]

        html_template_part2 = [
            # '    </div>\n',
            '    <div class="line_block">Total rating: {} </div>\n'.format(total_rating),
            '    <div class="line_block">\n',
        ]

        html_template_part3 = [
            '    </div>\n',
            '  </body>\n',
            '</html>\n'
        ]

        html_template_info = [
             '    <div class="line_block"> Astra version: {} <br> Kernel: {} <br> Service count: {} <br> Load time execution: {} minutes</div>\n'.format(astra_version()[2], astra_version()[3], service_count, time_execution),
        ]

        with open('{}/main_report.html'.format(self.report_path), 'w') as total_html:
            total_html.writelines(html_template_part1)
            total_html.writelines(html_template_info)
            total_html.writelines(html_template_part2)
            total_html.writelines(graphs_in_total_html)
            total_html.writelines(html_template_part3)


    def data_to_dataframe_csv(self, data, filename):
        df = pd.DataFrame(data=data)
        df.to_csv('{path}/{file_name}.csv'.format(path=self.report_path, file_name=filename), index=False)


    @staticmethod
    def create_tar(path):
        '''
            tar архив с результатами тестирования
        '''
        time_mark = datetime.now().strftime("%d.%m.%Y_%H.%M")
        chdir(path)
        with tarfile.open('sng_{v}_{m}_{t}.tar'.format(v=astra_version()[2],
                                                         m=astra_version()[1],
                                                         t=time_mark), 'w') as tar:
            for file in listdir(path):
                tar.add('{}'.format(file))