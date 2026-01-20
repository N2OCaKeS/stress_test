import pandas as pd
from matplotlib import pyplot as plt

from allta import Criterion, MathModels

from exb_conf import REPORT_FILENAME, REPORT_PATH

class Report:
    def __init__(self, report_path=REPORT_PATH, type_test="smtp", img_width=16.256, img_height=12.192):
        self.report_path = report_path
        self.type_test = type_test
        self.width = img_width
        self.height = img_height

        with open(f"{REPORT_FILENAME}") as file:
            raw_data = file.read().split()

        if self.type_test == "smtp":
            self.mail_count = [int(param) for param in raw_data[::3]]
            self.successful = [int(param) for param in raw_data[1::3]]
            self.emails_per_second = [float(param) for param in raw_data[2::3]]
            self.raw_table = pd.DataFrame({'mail_count': self.mail_count,
                                           'successful': self.successful,
                                           'emails_per_second': self.emails_per_second})
        elif self.type_test == "imap":
            self.user_count = [int(param) for param in raw_data[::5]]
            self.successful_count = [int(param) for param in raw_data[1::5]]
            self.error_count = [int(param) for param in raw_data[2::5]]
            self.avg_latency = [float(param) for param in raw_data[3::5]]
            self.throughput = [float(param) for param in raw_data[4::5]]
            self.raw_table = pd.DataFrame({'user_count': self.user_count,
                                           'successful_count': self.successful,
                                           'error_count': self.error_count,
                                           'avg_latency': self.avg_latency,
                                           'throughput': self.throughput})

    @staticmethod
    def cm_to_inch(value):
        return value / 2.54

    def create_graph(self, x, y, filename, title_graph, x_label, y_label):
        fig, ax = plt.subplots(figsize=(self.cm_to_inch(self.width), self.cm_to_inch(self.height)))
        ax.plot(x, y)
        ax.set_yscale("linear")
        ax.set_title(title_graph)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.grid(True)
        fig.savefig(f'{REPORT_PATH}/{filename}.png')


    # TODO поменять критерии в зависимости от сценария теста
    def get_total_rating(self):
        if self.type_test == "smtp":
            criterions = [
                Criterion(name="successful", values=self.raw_table['successful'].tolist(), weight=0.3, sign=1, lower_bound=0, upper_bound=10000),
                Criterion(name="emails_per_second", values=self.raw_table["emails_per_second"].tolist(), weight=0.8, sign=1, lower_bound=0, upper_bound=4000),
            ]
        elif self.type_test == "imap":
            criterions = [
                Criterion(name="successful_count", values=self.raw_table['successful_count'].tolist(), weight=0.333, sign=1, lower_bound=0, upper_bound=...),
                Criterion(name="avg_latency", values=self.raw_table["avg_latency"].tolist(), weight=0.333, sign=-1, lower_bound=0, upper_bound=...),
                Criterion(name="throughput", values=self.raw_table["throughput"].tolist(), weight=0.333, sign=1, lower_bound=0, upper_bound=...),
            ]
        total_rating, s = MathModels.total_rating(criteria=criterions)
        return total_rating


        
