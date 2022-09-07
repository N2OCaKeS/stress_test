import pandas
import mpld3
import tarfile
from os import listdir
from time import time
from matplotlib import pyplot as plt
from libs.libpsb import astra_version
from psb_conf import REPORT_FILENAME, REPORT_PATH
from pretty_html_table import build_table
import numpy as np
import warnings


class Report:

    def __init__(self, param_name='clients', report_file=REPORT_FILENAME, all_params=False):
        with open(report_file, 'r') as report_file:
            raw_data = report_file.read().split()
        if all_params:
            self.raw_table = pandas.DataFrame({'scale': [int(param) for param in raw_data[0::7]],
                                               'transactions': [int(param) for param in raw_data[1::7]],
                                               'threads': [int(param) for param in raw_data[2::7]],
                                               'clients': [int(param) for param in raw_data[3::7]],
                                               'la': [float(la) for la in raw_data[4::7]],
                                               'tps1': [float(tps1) for tps1 in raw_data[5::7]],
                                               'tps2': [float(tps2) for tps2 in raw_data[6::7]]})
        else:
            self.raw_table = pandas.DataFrame({param_name: [int(param) for param in raw_data[0::4]],
                                               'la': [float(la) for la in raw_data[1::4]],
                                               'tps1': [float(tps1) for tps1 in raw_data[2::4]],
                                               'tps2': [float(tps2) for tps2 in raw_data[3::4]]})

    @staticmethod
    def data_from_file(report_file=REPORT_FILENAME):
        with open(report_file, 'r') as file:
            raw_data = file.read().split()
        return ([int(param) for param in raw_data[0::4]],
                [float(la) for la in raw_data[1::4]],  # latency average data
                [float(tps1) for tps1 in raw_data[2::4]],  # tps including connections establishing data
                [float(tps2) for tps2 in raw_data[3::4]])  # tps excluding connections establishing data

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

    def create_beauty_table(self, path=REPORT_PATH, table_name='psb_report_table.html'):
        beauty_table = build_table(self.raw_table, 'blue_light')
        with open('{}/{}'.format(path, table_name), 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)

    '''
        Графики для теста: 'Нахождение предельного числа клиентов' 
    '''
    def create_psb_cl_la_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['la']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[1])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o', x, f(x))
        plt.title('{}({}). Clients/Latency average'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Clients')
        plt.ylabel('Latency average')
        plt.grid(True)
        plt.savefig('{}/psb_cl_la_graph'.format(path))

    def create_psb_cl_tps1_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['tps1']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[2])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o', x, f(x))
        plt.title('{}({}). Clients/TPS(including connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Clients')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_cl_tps1_graph'.format(path))

    def create_psb_cl_tps2_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['tps2']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[3])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o', x, f(x))
        plt.title('{}({}). Clients/TPS(excluding connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Clients')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_cl_tps2_graph'.format(path))

    def create_psb_cl_tpsall_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['tps1', 'tps2']]

        # build graph
        plt.figure()
        plt.plot(x, y)
        plt.title('{}({}). Clients/TPS'.format(astra_version()[0], astra_version()[1]))
        plt.legend(['TPS(including connections establishing)', 'TPS(excluding connections establishing)'])
        plt.xlabel('Clients')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_cl_tpsall_graph'.format(path))

    '''
        Графики для теста: 'Нахождение предельного коэффициента масштаба' 
    '''
    def create_psb_sc_la_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['scale']]
        y = self.raw_table.loc[:, ['la']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[1])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o')
        plt.title('{}({}). Scale/Latency average'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Scale')
        plt.ylabel('Latency average')
        plt.grid(True)
        plt.savefig('{}/psb_sc_la_graph'.format(path))

    def create_psb_sc_tps1_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['scale']]
        y = self.raw_table.loc[:, ['tps1']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[2])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o')
        plt.title('{}({}). Scale/TPS(including connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Scale')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_sc_tps1_graph'.format(path))

    def create_psb_sc_tps2_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['scale']]
        y = self.raw_table.loc[:, ['tps2']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[3])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o')
        plt.title('{}({}). Scale/TPS(excluding connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Scale')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_sc_tps2_graph'.format(path))

    def create_psb_sc_tpsall_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['scale']]
        y = self.raw_table.loc[:, ['tps1', 'tps2']]

        # build graph
        plt.figure()
        plt.plot(x, y)
        plt.title('{}({}). Scale/TPS'.format(astra_version()[0], astra_version()[1]))
        plt.legend(['TPS(including connections establishing)', 'TPS(excluding connections establishing)'])
        plt.xlabel('Scale')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_sc_tpsall_graph'.format(path))

    '''
        Графики для теста: 'Нахождение предельного числа транзакций' 
    '''
    def create_psb_tr_la_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['transactions']]
        y = self.raw_table.loc[:, ['la']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[1])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o')
        plt.title('{}({}). Transactions/Latency averege'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Transactions')
        plt.ylabel('Latency average')
        plt.grid(True)
        plt.savefig('{}/psb_tr_la_graph'.format(path))

    def create_psb_tr_tps1_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['transactions']]
        y = self.raw_table.loc[:, ['tps1']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[2])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o')
        plt.title('{}({}). Transactions/TPS(including connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Transactions')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_tr_tps1_graph'.format(path))

    def create_psb_tr_tps2_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['transactions']]
        y = self.raw_table.loc[:, ['tps2']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[3])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o')
        plt.title('{}({}). Transactions/TPS(excluding connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Transactions')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_tr_tps2_graph'.format(path))

    def create_psb_tr_tpsall_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['transactions']]
        y = self.raw_table.loc[:, ['tps1', 'tps2']]

        # build graph
        plt.figure()
        plt.plot(x, y)
        plt.title('{}({}). Transactions/TPS'.format(astra_version()[0], astra_version()[1]))
        plt.legend(['TPS(including connections establishing)', 'TPS(excluding connections establishing)'])
        plt.xlabel('Transactions')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_tr_tpsall_graph'.format(path))

    '''
        Графики для теста: 'Нахождение предельного числа потоков' 
    '''
    def create_psb_th_la_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['threads']]
        y = self.raw_table.loc[:, ['la']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[1])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o')
        plt.title('{}({}). Threads/Latency averege'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Threads')
        plt.ylabel('Latency average')
        plt.grid(True)
        plt.savefig('{}/psb_th_la_graph'.format(path))

    def create_psb_th_tps1_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['threads']]
        y = self.raw_table.loc[:, ['tps1']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[2])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o')
        plt.title('{}({}). Threads/TPS(including connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Threads')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_th_tps1_graph'.format(path))

    def create_psb_th_tps2_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['threads']]
        y = self.raw_table.loc[:, ['tps2']]

        data_arrays = self.data_from_file()
        f = self.data_aproximation(data_arrays[0], data_arrays[3])

        # build graph
        plt.figure()
        plt.plot(x, y, 'o')
        plt.title('{}({}). Threads/TPS(excluding connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Threads')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_th_tps2_graph'.format(path))

    def create_psb_th_tpsall_graph(self, path=REPORT_PATH):
        x = self.raw_table.loc[:, ['threads']]
        y = self.raw_table.loc[:, ['tps1', 'tps2']]

        # build graph
        plt.figure()
        plt.plot(x, y)
        plt.title('{}({}). Threads/TPS'.format(astra_version()[0], astra_version()[1]))
        plt.legend(['TPS(including connections establishing)', 'TPS(excluding connections establishing)'])
        plt.xlabel('Threads')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_th_tpsall_graph'.format(path))

    @staticmethod
    def merge(table_lst, graph_lst, path=REPORT_PATH):
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
            '    <title>PostgreSQL report</title>\n',
            '    <link href="./style.css" rel="stylesheet" type="text/css">\n',
            '  </head>\n',
            '  <body>\n',
            '    <style>\n',
            '        .line_block {\n',
            '                width:45%;\n',
            '                height:90%;\n',
            '                background:#f1f1f1;\n',
            '                float:left;\n',
            '                margin: 0 15px 15px 0;\n',
            '                text-align:center;\n',
            '                padding: 10px;\n',
            '                }\n',
            '        .table_block {\n',
            '                width:95%;\n',
            '                height:100%;\n',
            '                background:#4169E1;\n',
            '                float:left;\n',
            '                margin: 1%;\n',
            '                text-align:center;\n',
            '                padding: 10px;\n',
            '                }\n',
            '        .graph_block {\n',
            '                width:95%;\n',
            '                height:100%;\n',
            '                background:#4169E1;\n',
            '                margin: 1%;\n',
            '                text-align:center;\n',
            '                padding: 10px;\n',
            '                }\n',
            '    </style>\n',
            '    <h1>Stress testing PostgreSQL</h1>\n',
            '    <div style="width:50%; height:1px; clear:both;"></div>\n',
            '    <div class="line_block">\n',
        ]

        html_template_part2 = [
            '    </div>\n',
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
    def create_tar(path=REPORT_PATH):
        '''
            tar архив с результатами тестирования
        '''
        with tarfile.open('report{v}_{m}_{t}.tar'.format(v=astra_version()[0],
                                                         m=astra_version()[1],
                                                         t=time()), 'w') as tar:
            for file in listdir(path):
                tar.add('{}/{}'.format('report', file))
