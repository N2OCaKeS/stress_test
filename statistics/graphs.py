import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from functools import reduce
from abc import abstractmethod

from typetest import TypeTest

from logging_conf import main_logger

"""
    TODO Необходимо реализовать интерактивный график, смотреть запись техсреды от ОНИ (который работал в Apple)
"""
class Colors:
    BAD = "#ea5c76"
    WARNING = "#ffc322"
    GOOD = "#c7d84c"


class FigSize:
    WIDTH = 16
    HEIGHT = 9


class Graphs:
    @abstractmethod
    def draw() -> None:
        pass


class MainGraph(Graphs):
    """
        Класс предназначен для построение главной столбчатой диаграммы
    """
    def __init__(self, list_of_score, scale_txt, type_test, saver, ylabel = "Значение рейтинга"):
        self.list_of_score = list_of_score
        self.ylabel = ylabel
        self.grid = False
        self.scale_x = [x for x in range(1, len(self.list_of_score) + 1, 1)]
        self.scale_txt = scale_txt
        self.type_test = type_test
        self.saver = saver
        main_logger.debug(f"Тип теста: {self.type_test}")
        main_logger.debug(f"Отработал конструктор scale_x = {self.scale_x}, шкала = {self.scale_txt}")
        

    def _get_colors(self):
        colors = []
        for score_value in self.list_of_score:
            if score_value < np.mean(self.list_of_score) - 1.5 * np.std(self.list_of_score):
                    colors.append(Colors.BAD)
            elif score_value > np.mean(self.list_of_score) + 1.5 * np.std(self.list_of_score):
                colors.append(Colors.WARNING)
            else:
                colors.append(Colors.GOOD)
        return colors
    
    def _create_legend(self):
        red_patch = mpatches.Patch(color=Colors.BAD, label='Рейтинг ниже мат. ожидания на величину x1.5 превышающую стандартное отклонение')
        green_patch = mpatches.Patch(color=Colors.GOOD, label='Рейтинг соответвует доверительному интервалу')
        yellow_patch = mpatches.Patch(color=Colors.WARNING, label='Рейтинг выше мат. ожидания на величину x1.5 превышающую стандартное отклонение')
        return [red_patch, green_patch, yellow_patch]
    
    def draw(self):
        fig, ax = plt.subplots(figsize=(FigSize.WIDTH, FigSize.HEIGHT))
        rect = ax.bar(self.scale_x, self.list_of_score, color=self._get_colors())
        ax.grid(self.grid)
        ax.set_xticks(self.scale_x)
        lower, upper = 0, max(self.list_of_score) + max(self.list_of_score) * 0.15
        main_logger.debug(f"Нижняя граница = {lower}, верхняя граница = {upper}")
        if upper - lower < 1e-5:  # или любое другое малое значение, которое вам кажется подходящим
            upper += 0.1  # или любое другое значение, которое создаст достаточное разнообразие
        ax.set_ylim([lower, upper])
        plt.gca().set_xticklabels(self.scale_txt, rotation=20, horizontalalignment='right')
        main_logger.debug("Задан угол наклон шкалы 20 градусов, горизонтальное выравнивание справа")
        ax.set_ylabel(self.ylabel)
        ax.set_title(f"{TypeTest.get_full_name_test_without_df(self.type_test)}. Сравнительная диаграмма значений рейтингов, \nвычисленных на основании результатов нагрузочного тестирования.")
        ax.bar_label(rect, label_type="center", fmt="%d", rotation=90, fontweight=500)
        ax.legend(handles=self._create_legend())
        """
            TODO Сделать сохранение графика
        """
        self.saver.save(plot=plt, 
                        name=f"{self.type_test}_{self.__class__.__name__}")
        main_logger.info(f"Сохранен {self.__class__.__name__} для {self.type_test}")
        plt.close()
        

class MainGraphH(MainGraph):
    """
        Класс предназначен для построение ПЕРЕВЕРНУТОЙ главной столбчатой диаграммы
    """
    def __init__(self, list_of_score, scale_txt, type_test, saver, xlabel = "Значение рейтинга"):
        self.xlabel = xlabel
        super().__init__(list_of_score, scale_txt, type_test, saver, xlabel)

    def draw(self):
        fig, ax = plt.subplots(figsize=(FigSize.WIDTH, FigSize.HEIGHT))
        pos = ax.get_position()
        new_pos = [pos.x0 + 0.05, pos.y0, pos.width, pos.height]  # Сдвиг на 0.1 вправо
        ax.set_position(new_pos)

        rect = ax.barh(self.scale_x, self.list_of_score, color=self._get_colors())
        ax.grid(self.grid)
        ax.set_yticks(self.scale_x)
        ax.set_ylim([0, max(self.scale_x) + 6])
        plt.gca().set_yticklabels(self.scale_txt, rotation=0, horizontalalignment='right')
        main_logger.debug("Задан угол наклон шкалы 0 градусов, горизонтальное выравнивание справа")
        ax.set_xlabel(self.ylabel)

        ax.set_title(f"{TypeTest.get_full_name_test_without_df(self.type_test)}. Сравнительная диаграмма значений рейтингов, \nвычисленных на основании результатов нагрузочного тестирования.")
        ax.bar_label(rect, label_type="center", fmt="%d", rotation=0, fontweight=500)
        ax.legend(handles=self._create_legend())
        self.saver.save(plot=plt, 
                        name=f"{self.type_test}_{self.__class__.__name__}")
        main_logger.info(f"Сохранен {self.__class__.__name__} для {self.type_test}")
        plt.close()




class SummaryGraph(Graphs):
    """
        Класс предназначен для построения графика сравнений с любым количеством данных
        Например:
        1) [XFS, EXT4, XFS_parsec]
        2) [XFS]
        3) [XFS, EXT4, XFS_parsec, EXT4_parsec, NTFS]
    """
    #######################################################
    """
      TODO Добавить цвета
    """
    def __init__(self, saver, stand_grade, comparison_scale_of_score: list, scale_txt, comparison_names: list, graph_name: str, colors: list = ['#88c1f2', '#ea5c76']):
        self.saver = saver
        self.stand_grade = stand_grade
        self.comparison_scale_of_score = comparison_scale_of_score
        self.scale_txt = scale_txt
        self.comparison_names = comparison_names
        self.graph_name = graph_name
        self.colors = colors
        self.comparison_len = len(comparison_scale_of_score)
        self.bar_width = 0.8
        self.scale_x = np.arange(len(scale_txt))

    def draw(self, *args, **kwargs) -> None:
        fig, ax = plt.subplots(figsize=(FigSize.WIDTH, FigSize.HEIGHT))
        max_score = 1 # Единица чтобы не было предупреждения  "UserWarning: Attempting to set identical low and high ylims makes transformation singular; automatically expanding"
        for ind, scale_of_score in enumerate(self.comparison_scale_of_score):
            if max_score < max(scale_of_score):
                max_score = max(scale_of_score)
    
            width = self.bar_width / self.comparison_len
            expression = self.scale_x + ind * width - width * (len(self.comparison_scale_of_score) - 1) /2
            rects = ax.bar(expression, scale_of_score, 
                           color=self.colors[ind],
                           alpha=0.8, 
                           width=width,
                           label=self.comparison_names[ind])
            ax.bar_label(rects, label_type="center", fmt="%d", rotation=90)
            main_logger.debug(f"Накидываем ind={ind} bar в {self.__class__.__name__}")

        ax.set_ylim([0, max_score + max_score * 0.15])
        ax.set_xticks(self.scale_x)
        ax.set_title(f"{" - ".join(self.comparison_names)}.\n{self.stand_grade}\nСравнительная диаграмма значений, \nвычисленных на основании результатов нагрузочного тестирования.")
        ax.set_xticklabels(self.scale_txt, rotation=20, horizontalalignment='right')
        ax.legend()
        fig.tight_layout()

        if kwargs.get("y_label"):
            y_label = kwargs.get("y_label")
            ax.set_ylabel(y_label)
            main_logger.debug("в kwargs было передано y_label, задаем описание y_label")

        if kwargs.get("graph_ind") or kwargs.get("graph_ind") == 0:
            graph_name = f"{self.graph_name}_{kwargs.get("graph_ind")}"
            main_logger.debug(f"Имя графика (должно быть вместе с индексом): {graph_name}")
        else:
            graph_name = f"{self.graph_name}"
            main_logger.debug(f"Имя графика (должно быть без индекса): {graph_name}")

        self.saver.save(plot=plt, name=f"{graph_name}_{self.__class__.__name__}")
        main_logger.info(f"Сохранен {self.__class__.__name__} для {' '.join(self.comparison_names)}")
        plt.close()


class SummaryLineGraph(SummaryGraph):
    def draw(self):
        legend = []
        fig, ax = plt.subplots(figsize=(FigSize.WIDTH, FigSize.HEIGHT))
        max_score = 1
        for ind, scale_of_score in enumerate(self.comparison_scale_of_score):
            if max_score < max(scale_of_score):
                max_score = max(scale_of_score)
            ax.plot(self.scale_x, scale_of_score, "o-", color=self.colors[ind])
            main_logger.debug(f"Накидываем plot в {self.__class__.__name__}")
            legend.append(self.comparison_names[ind])
        
        ax.set_ylim([0, max_score + max_score * 0.15])
        main_logger.debug("Назначаем ylim = 0...max_score + max_score * 0.15")
        ax.set_xticks(self.scale_x)
        ax.set_title(f"{" - ".join(self.comparison_names)}.\n{self.stand_grade}\nСравнительная диаграмма значений, \nвычисленных на основании результатов нагрузочного тестирования.")
        ax.set_xticklabels(self.scale_txt, rotation=20, horizontalalignment='right')
        ax.legend(legend)
        fig.tight_layout()
        self.saver.save(plot=plt, name=f"{self.graph_name}_{self.__class__.__name__}")
        main_logger.info(f"Сохранен {self.__class__.__name__} для {' '.join(self.comparison_names)}")
        plt.close()


class ComparisonKernelLineGraph(Graphs):
    def __init__(self, separate_by_kernel_data, type_test, saver):
        self.separate_by_kernel_data = separate_by_kernel_data
        self.type_test = type_test
        self.saver = saver

    def draw(self, *args, **kwargs):
        dataframes = [p for p in self.separate_by_kernel_data.values()]
        sfx_tuple = ("x", "y", "z", "w", "e", "t", "u", "i")
        sfx_iter = iter(sfx_tuple)
        merged_df = reduce(lambda left, right: pd.merge(left, right, on=["Релиз", "Стенд"], how='outer', suffixes=(f'_{next(sfx_iter)}', '')), dataframes)
        ratings_for_plt_graph = merged_df.iloc[::, 3::2]
            
        fig, ax = plt.subplots(figsize=(12.8, 7.2))
        ax.grid(True, alpha=.6)
        if kwargs.get("y_label"):
            y_label = kwargs.get("y_label")
            ax.set_ylabel(y_label)
            main_logger.debug("в kwargs было передано y_label, задаем описание y_label")
        ax.set_title(f"Линейная диаграмма сравнения по ядрам.\n{self.type_test}")
        colors = ['#f90829', '#007b7a', '#f9b312', '#c7d84c', 'green', 'red']
        for index in range(ratings_for_plt_graph.shape[1]):
            ax.plot(merged_df['Релиз'], ratings_for_plt_graph.iloc[::, index], "o-", color=colors[index])
        plt.legend(self.separate_by_kernel_data.keys())

        # Lighten borders
        
        plt.gca().spines["top"].set_alpha(.0)
        plt.gca().spines["bottom"].set_alpha(.3)
        plt.gca().spines["right"].set_alpha(.0)
        plt.gca().spines["left"].set_alpha(.3)
        main_logger.debug("Задали осветление границ")

        if kwargs.get("graph_ind") or kwargs.get("graph_ind") == 0:
            graph_name = f"{self.type_test}_{kwargs.get("graph_ind")}"
            main_logger.debug(f"Имя графика (должно быть вместе с индексом): {graph_name}")
        else:
            graph_name = f"{self.type_test}"
            main_logger.debug(f"Имя графика (должно быть без индекса): {graph_name}")

        self.saver.save(plot=plt, name=f"{graph_name}_{self.__class__.__name__}")
        main_logger.info(f"Сохранен {self.__class__.__name__} для {self.type_test}")
        plt.close()