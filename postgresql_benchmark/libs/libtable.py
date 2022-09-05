import pandas
import mpld3
from matplotlib import pyplot as plt
from libs.libpsb import astra_version
from pretty_html_table import build_table


class Report:

    def __init__(self):
        with open('../report/psb_report.txt', 'r') as report_file:
            raw_data = report_file.read().split()
        self.raw_table = pandas.DataFrame({'clients': [int(client_number) for client_number in raw_data[0::4]],
                                           'la': [float(la) for la in raw_data[1::4]],
                                           'tps1': [float(tps1) for tps1 in raw_data[2::4]],
                                           'tps2': [float(tps2) for tps2 in raw_data[3::4]]})

    @staticmethod
    def data_from_file():
        with open('../report/psb_report.txt', 'r') as report_file:
            raw_data = report_file.read().split()
        return ([int(client_number) for client_number in raw_data[0::4]],  # clients
                [float(la) for la in raw_data[1::4]],  # latency average data
                [float(tps1) for tps1 in raw_data[2::4]],  # tps including connections establishing data
                [float(tps2) for tps2 in raw_data[3::4]])  # tps excluding connections establishing data

    def create_beauty_table(self):
        beauty_table = build_table(self.raw_table, 'blue_light')
        with open('../report/psb_report.html', 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)

    def create_psb_plot_cl_la(self):
        '''
            Шаблон графика psb_plot_cl_la:
        '''
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['la']]
        plt.figure()
        plt.plot(x, y)
        plt.title('{}({}). Clients/Latency averege'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Clients')
        plt.ylabel('Latency averege')
        plt.grid(True)
        plt.savefig('../report/psb_plot_cl_la')

    def create_psb_plot_cl_tps1(self):
        '''
            Шаблон графика psb_plot_cl_tps1
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
        plt.savefig('../report/psb_plot_cl_tps1')

    def create_psb_plot_cl_tps2(self):
        '''
            Шаблон графика psb_plot_cl_tps2
        '''
        x = self.raw_table.loc[:, ['clients']]
        y = self.raw_table.loc[:, ['tps2']]
        plt.figure()
        plt.plot(x, y)
        plt.title(
            '{}({}). Clients/TPS(excluding connections establishing)'.format(astra_version()[0], astra_version()[1]))
        plt.xlabel('Clients')
        plt.ylabel('TPS')
        plt.grid(True)
        plt.savefig('../report/psb_plot_cl_tps2')

    def create_psb_plot_cl_tpsall(self):
        '''
            Шаблон графика psb_plot_cl_tpsall
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
        plt.savefig('../report/psb_plot_cl_tpsall')

    @staticmethod
    def merge_plots_and_tables():
        pass

    def create_full_report(self):
        self.create_beauty_table()
        self.create_psb_plot_cl_la()
        self.create_psb_plot_cl_tps1()
        self.create_psb_plot_cl_tps2()
        self.create_psb_plot_cl_tpsall()
        self.merge_plots_and_tables()



