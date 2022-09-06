import pandas
import mpld3
from matplotlib import pyplot as plt
from libs.libpsb import astra_version
from psb_conf import REPORT_FILENAME, REPORT_PATH
from pretty_html_table import build_table


class Report:

    def __init__(self, report_file=REPORT_FILENAME):
        with open(report_file, 'r') as report_file:
            raw_data = report_file.read().split()
        self.raw_table = pandas.DataFrame({'clients': [int(client_number) for client_number in raw_data[0::4]],
                                           'la': [float(la) for la in raw_data[1::4]],
                                           'tps1': [float(tps1) for tps1 in raw_data[2::4]],
                                           'tps2': [float(tps2) for tps2 in raw_data[3::4]]})

    @staticmethod
    def data_from_file(report_file=REPORT_FILENAME):
        with open(report_file, 'r') as file:
            raw_data = file.read().split()
        return ([int(client_number) for client_number in raw_data[0::4]],  # clients
                [float(la) for la in raw_data[1::4]],  # latency average data
                [float(tps1) for tps1 in raw_data[2::4]],  # tps including connections establishing data
                [float(tps2) for tps2 in raw_data[3::4]])  # tps excluding connections establishing data

    def create_beauty_table(self, path=REPORT_PATH, table_name='psb_report_table.html'):
        beauty_table = build_table(self.raw_table, 'blue_light')
        with open('{}/{}'.format(path, table_name), 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)

    '''
        Графики для теста: 'Нахождение предельного числа клиентов' 
    '''
    def create_psb_cl_la_graph(self):
        '''
            Шаблон графика psb_graph_cl_la:
        '''
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['la']]
        plt.figure()
        plt.plot(x, y)
        plt.title('{}({}). Clients/Latency averege'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Clients')
        plt.ylabel('Latency averege')
        plt.grid(True)
        plt.savefig('{}/psb_cl_la_graph'.format(REPORT_PATH))

    def create_psb_cl_tps1_graph(self):
        '''
            Шаблон графика psb_graph_cl_tps1
        '''
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['tps1']]
        plt.figure()
        plt.plot(x, y)
        plt.title(
            '{}({}). Clients/TPS(including connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Clients')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_cl_tps1_graph'.format(REPORT_PATH))

    def create_psb_cl_tps2_graph(self):
        '''
            Шаблон графика psb_graph_cl_tps2
        '''
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['tps2']]
        plt.figure()
        plt.plot(x, y)
        plt.title('{}({}). Clients/TPS(excluding connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Clients')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_cl_tps2_graph'.format(REPORT_PATH))

    def create_psb_cl_tpsall_graph(self):
        '''
            Шаблон графика psb_graph_cl_tpsall
        '''
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['tps1', 'tps2']]
        plt.figure()
        plt.plot(x, y)
        plt.title('{}({}). Clients/TPS'.format(astra_version()[0], astra_version()[1]))
        plt.legend(['TPS(including connections establishing)', 'TPS(excluding connections establishing)'])
        plt.xlabel('Clients')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('{}/psb_cl_tpsall_graph'.format(REPORT_PATH))

    '''
        TODO:
        Графики для теста: 'Нахождение предельного коэффициента масштаба'
    '''
    def create_psb_sc_la_graph(self):
        pass

    def create_psb_sc_tps1_graph(self):
        pass

    def create_psb_sc_tps2_graph(self):
        pass

    def create_psb_sc_tpsall_graph(self):
        pass

    ''' 
        TODO:
        Графики для теста: 'Нахождение предельного числа транзакций'
    '''
    def create_psb_tr_la_graph(self):
        pass

    def create_psb_tr_tps1_graph(self):
        pass

    def create_psb_tr_tps2_graph(self):
        pass

    def create_psb_tr_tpsall_graph(self):
        pass

    ''' 
        TODO:
        Графики для теста: 'Нахождение предельного числа потоков'
    '''
    def create_psb_th_la_graph(self):
        pass

    def create_psb_th_tps1_graph(self):
        pass

    def create_psb_th_tps2_graph(self):
        pass

    def create_psb_th_tpsall_graph(self):
        pass

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
            graphs_in_total_html.append('<div class="graph_block"><img src="{}/{}"></div>\n'.format(path, graph))

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

