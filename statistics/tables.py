
import re
import json
import requests
import pandas as pd
import numpy as np
from abc import abstractmethod
from functools import reduce

from confluence.confluence_conf import CONFLUENCE_URL, CONFLUENCE_SPACE
from errors import NoDataAvailableForThisTestType, NoBugsFoundForComponent, NoAnnotationsForComponent
from grade import Grade
from sorting import SortMainTable, SortUniqueMajorKernel
from typetest import TypeTest

from logging_conf import main_logger
from newlogging import task_logger

class Table:
    @abstractmethod
    def build():
        pass


class DataConversion:
    @staticmethod
    def conversion(data):
        pass


class MainTable(Table):
    """
        Класс для построения главной сравнительной таблицы
    """
    def __init__(self, data, saver, columns: list = None, columns_scores: list = ['Рейтинг'], stat_title=None, rc_version=None):
        self.data = data
        self.saver = saver
        self.columns_scores = columns_scores
        self.stat_title = stat_title
        self.rc_version = rc_version
        if not columns:
            self.columns = ['type_test', 'Релиз', 'Ядро', 'Режим защищенности', 'Стенд'] + self.columns_scores
        else:
            self.columns = ['type_test'] + columns + self.columns_scores

    def __combine_cells_for_build_link_to_the_report(self, ind, dataframe):
        title = "_".join(dataframe.iloc[ind - 1, [0, 1, 3, 2, 4]].astype(str))
        # set_titles = {"parsec", "vanilla", "balance", "auth", "time"}
        # for item in set_titles:
        #     if item in title:
        #         if "parsec_impact-fs-aud-off" in title:
        #             title = title.replace("parsec_impact-fs-aud-off", "parsec impact-fs aud-off")
        #             break
        #         title = title.replace("_", " ", 1)
        #         break
        link = f"https://{CONFLUENCE_URL}/display/{CONFLUENCE_SPACE}/" + title
        value_with_link = f'<a href="{link}">{ind}</a>'

        return value_with_link

    @task_logger(level=3)
    def build(self) -> pd.DataFrame:
        # Создаем оъект датафрема (таблицы) на основе наших данных
        
        df = pd.DataFrame(data=self.data)
        if df.empty:
            # print("DataFrame is empty.")
            # raise NoDataAvailableForThisTestType
            return
        
        df[self.columns_scores] = pd.DataFrame(df['score'].tolist(), index=df.index)
        df[self.columns_scores] = df[self.columns_scores].fillna(0).astype(float)
        df = df.drop(columns=['score'])
        df.columns = self.columns
        # Сортируем наши данные
        sort_kernel_df = SortUniqueMajorKernel.sort(old_dataframe=df)
        sort_df = SortMainTable.sort(dataframe=sort_kernel_df)

        # panda series для чего то нужна была пока не помню
        # panda_series = sort_df['Рейтинг']

        # ставим удобо читаемые индексы в таблицу
        sort_df.insert(0, "№", [self.__combine_cells_for_build_link_to_the_report(x, sort_df) for x in range(1, len(sort_df) + 1, 1)])

        # Меняем номер стенда на grade
        sort_df['Стенд'] = sort_df['Стенд'].apply(lambda x: Grade.get_grade(x))

        # Обрабатываем рейтинг
        # sort_df['Рейтинг'] = sort_df['Рейтинг'].apply(lambda x: float(x))
        self.saver.save(dataframe=sort_df.drop(columns=['type_test']),
                        name=f"{TypeTest.get_type_test(dataframe=df)}_{self.__class__.__name__}.html", 
                        desc=f"<h2>Сводная таблица результатов тестирования {TypeTest.get_full_name_test(dataframe=df)}</h2>")
        main_logger.info(f"Сохранена таблица {self.__class__.__name__}")
        # возвращаем отсортированную, правильную таблицу
        return sort_df


class MathTable(Table):
    """
        Класс для построения математической сравнительной таблицы
    """
    def __init__(self, dataframe, saver, stat_title=None, rc_version=None):
        self.dataframe = dataframe
        self.saver = saver
        self.data = dataframe.iloc[:, -1]
        self.min_value = min(self.data)
        self.max_value = max(self.data)
        self.mean = round(np.mean(self.data), 3)
        self.median = round(np.median(self.data), 3)
        self.std = round(np.std(self.data), 3)
        self.var = round(np.var(self.data), 3)
        self.stat_title = stat_title
        self.rc_version = rc_version

    def __create_math_array(self) -> np.array:
        return np.array(
                [
                    ['MIN', self.min_value],
                    ['MAX', self.max_value],
                    ['Мат. ожидание', self.mean],
                    ['Медиана', self.median],
                    ['Стандартное отклонение', self.std],
                    ['Дисперсия', self.var],
                ]
            )

    def __build_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(data=self.__create_math_array(), columns=["Оценка", "Значение"])
    
    @task_logger(level=3)
    def build(self):
        df = self.__build_dataframe()
        """
            TODO Проконтролировать передачу имени файла и описание заголовка перед таблицей
        """
        self.saver.save(dataframe=df, 
                        name=f"{TypeTest.get_type_test(dataframe=self.dataframe)}_{self.__class__.__name__}.html",
                        desc=f"<h2>Таблица основных статистических параметров {TypeTest.get_full_name_test(dataframe=self.dataframe)}</h2>")
        main_logger.info(f"Сохранена таблица {self.__class__.__name__}")


class SummaryTable(Table):
    def __init__(self, dataframes, stat_title=None, rc_version=None):
        self.dataframes = dataframes
        self.stat_title = stat_title
        self.rc_version = rc_version
        # self.saver = saver

    @task_logger(level=3)
    def build(self):
        df_merged = pd.merge(self.dataframes[0], self.dataframes[1],
                             how="outer",
                             left_on=["Релиз", "Ядро", "Стенд"],
                             right_on=["Релиз", "Ядро", "Стенд"])
        df_merged['Рейтинг_x'] = df_merged['Рейтинг_x'].fillna(0)
        df_merged['Рейтинг_y'] = df_merged['Рейтинг_y'].fillna(0)
        df = (df_merged["Стенд"].mode()[0], 
              df_merged["Рейтинг_x"], 
              df_merged["Рейтинг_y"], 
              df_merged['Релиз'] + '_' + df_merged['Ядро'])
        main_logger.info(f"Построена {self.__class__.__name__}")
        main_logger.debug(f"{df}")
        return df
    

class SummaryTableNew(Table):
    """
        TODO доделать, должна быть более универсальная чем SummaryTable
    """
    def __init__(self, dataframes, score = "Рейтинг", stat_title=None, rc_version=None):
        self.dataframes = dataframes
        self.score = score
        self.sfx_tuple = ("x", "y", "z", "w", "e", "t", "u", "i")
        self.stat_title = stat_title
        self.rc_version = rc_version

    @task_logger(level=3)
    def build(self):
        sfx_iter = iter(self.sfx_tuple)
        df_merged = reduce(
            lambda left, right: pd.merge(
                left[['Релиз', 'Ядро', 'Стенд', self.score]], 
                right[['Релиз', 'Ядро', 'Стенд', self.score]], 
                how='outer', 
                on=["Релиз", "Ядро", "Стенд"],
                suffixes=(f'_{next(sfx_iter)}', '')
            ),
            self.dataframes)
        main_logger.info(f"Построена {self.__class__.__name__}")
        main_logger.debug(f"{df_merged}")
        df_merged = df_merged.iloc[::-1]
        df_score = df_merged.drop(columns=['Релиз', 'Ядро', 'Стенд'])
        list_score = [df_score[col].fillna(0) for col in df_score.columns]
        # print(df_score.columns, flush=True)
        # print(list_score, flush=True)
        return (df_merged["Стенд"].mode()[0], 
                df_merged['Релиз'] + '_' + df_merged['Ядро'],
                list_score)


class TableSeparatelyByKernel(Table):
    def __init__(self, dataframe: pd.DataFrame, score: str, stat_title=None, rc_version=None):
        self.dataframe = dataframe
        self.score = score if score != None else "Рейтинг"
        self.stat_title = stat_title
        self.rc_version = rc_version

    @task_logger(level=3)
    def build(self):
        unique_kernels = list(self.dataframe['Ядро'].unique())
        keys_kernel = SortUniqueMajorKernel.groupby_uniq_kernel(uniq_kernels=unique_kernels)
        separate_by_kernel_df = dict()
        # print(self.score)
        for kernel in keys_kernel:
            if type(self.score) == type(list()):
                separate_by_kernel_df[kernel] = SortUniqueMajorKernel.filter_by_kernel_version(df=self.dataframe, kernel=kernel)[['Релиз', 'Ядро', 'Стенд'] + self.score]
            else:
                separate_by_kernel_df[kernel] = SortUniqueMajorKernel.filter_by_kernel_version(df=self.dataframe, kernel=kernel)[['Релиз', 'Ядро', 'Стенд', self.score]]
                

        main_logger.info(f"Построена {self.__class__.__name__}")
        main_logger.debug(f"{separate_by_kernel_df}")
        return separate_by_kernel_df
    

class BugsTable(Table):
    def __init__(self, saver, component, stat_title=None, rc_version=None):
        self.saver = saver
        self.component = component
        self.url = 'http://allta.devos.astralinux.ru/rest/api/known-bugs'
        self.response = requests.get(url=self.url)
        self.stat_title = stat_title
        self.rc_version = rc_version

    @task_logger(level=3)
    def build(self):
        if self.response.status_code == 200:
            data = self.response.json()

            macros = """<ac:structured-macro ac:name="jira" ac:schema-version="1" ac:macro-id="e586f42e-cab2-426c-97d5-692afd3dee26">
                <ac:parameter ac:name="server">Jira - Astra Linux</ac:parameter>
                <ac:parameter ac:name="serverId">d19f6132-65dc-37bb-94ca-4be05d9bb688</ac:parameter>
                <ac:parameter ac:name="key">{task_id}</ac:parameter>
                <ac:parameter ac:name="columns">key,summary,type,created,updated,due,assignee,reporter,priority,status,resolution</ac:parameter>
                </ac:structured-macro>"""
            rows = []
            for component, tasks in data.items():
                for task_id, link in tasks.items():
                    rows.append({"Компонент": component,
                                 "Ошибка": macros.format(task_id=task_id)
                                 })
            
            df = pd.DataFrame(rows)
            """
                Сортировка по компоненту на данный момент отключена
            """
            df = df[df['Компонент'] == self.component]
            if df.empty:
                raise NoBugsFoundForComponent
            df['№'] = range(1, df.shape[0] + 1)
            df = df[["№", "Ошибка"]]
            self.saver.save(dataframe=df,
                            name=f"{self.__class__.__name__}.html",
                            desc=f"<h2>Таблица найденных ошибок</h2>")
            main_logger.info(f"Сохранена таблица {self.__class__.__name__}")


class Annotations(Table):
    def __init__(self, saver, component, stat_title=None, rc_version=None):
        self.saver = saver
        self.component = component
        self.url = 'http://allta.devos.astralinux.ru/rest/api/annotations'
        self.response = requests.get(url=self.url)
        self.stat_title = stat_title
        self.rc_version = rc_version
    
    @task_logger(level=3)
    def build(self):
        if self.response.status_code == 200:
            data = self.response.json()
            try:
                text_annotation = data[self.component]
            except KeyError:
                raise NoAnnotationsForComponent
            
            # macros = r"""<ac:structured-macro ac:name="jira" ac:schema-version="1" ac:macro-id="e586f42e-cab2-426c-97d5-692afd3dee26">
            #     <ac:parameter ac:name="server">Jira - Astra Linux</ac:parameter>
            #     <ac:parameter ac:name="serverId">d19f6132-65dc-37bb-94ca-4be05d9bb688</ac:parameter>
            #     <ac:parameter ac:name="key">\1</ac:parameter>
            #     <ac:parameter ac:name="columns">key,summary,type,created,updated,due,assignee,reporter,priority,status,resolution</ac:parameter>
            #     </ac:structured-macro>"""

            pattern = r'\b(BT-\d+)\b'
            replacement = r'<a href="https://jira.astralinux.ru/browse/\1">\1</a>'
            result = re.sub(pattern, replacement, text_annotation)

            # print(result, flush=True)
            self.saver.save(text=result, name=f"{self.__class__.__name__}.html")
            main_logger.info(f"Сохранена аннотация для {self.component}")
            
