import os
import pandas as pd
import shutil
from abc import abstractmethod

from confluence.confluence_conf import ID_ROOT_PAGES
from pages import Pages
from parsers import MainParser, BaseParser, FreeIpaParser, VirtParser, ParsecParser, PostgreSQLParser, DockerParser, FileSystemParser
from tables import MainTable, MathTable, SummaryTable, TableSeparatelyByKernel, SummaryTableNew, BugsTable, Annotations
from graphs import MainGraph, SummaryGraph, SummaryLineGraph, ComparisonKernelLineGraph
from sorting import Scale
from savers import SaveTableToFile, SaveGraph, SaveText
from uploaders import BaseUploader
from typetest import TypeTest
from errors import NoDataAvailableForThisTestType, NoBugsFoundForComponent, NoAnnotationsForComponent

from logging_conf import main_logger
from newlogging import task_logger


class Statistics:
     @abstractmethod
     def create():
          pass


class BaseStatistics(Statistics):
     def __init__(self, stat_title, username, tokenconf, set_of_test_types: set, comparison_list: list = None, comparison_kernel_list: list = None, score_parser = BaseParser):
          self.stat_title = stat_title
          self.username = username
          self.tokenconf = tokenconf
          self.set_of_test_types = set_of_test_types
          self.comparison_list = comparison_list
          self.comparison_kernel_list = comparison_kernel_list
          self.score_parser = score_parser
          if "/" in  self.stat_title:
                    self.stat_title = self.stat_title.replace("/", "-") 
          if not self.stat_title in os.listdir():
               os.mkdir(self.stat_title)
               os.mkdir(f"{self.stat_title}/statistics")
               os.mkdir(f"{self.stat_title}/statistics_rc")
          self.stat_title = self.stat_title.replace("-", "/")
          self.info_for_log = {
               "username": self.username,
               "stat_title": self.stat_title,
               "rc_version": None,
               "type_test": None
          }
          main_logger.info(f"Отработал конструктор {self.__class__.__name__}, {self.stat_title}")
          
     def _get_pages(self):
          confluence_obj = Pages(username=self.username, token=self.tokenconf)
          all_pages, rc_all_pages = [], {}
          for id_root_page in ID_ROOT_PAGES:
               pages_major_update, rc_pages_major_update = confluence_obj.get_list_pages(id_root_page=id_root_page, 
                                                                                         stat_title=self.stat_title)
               all_pages.extend(pages_major_update)
               rc_all_pages.update(rc_pages_major_update)
          main_logger.debug(f"ID всех страниц - {all_pages};\nID всех страниц RC и имена их родителей - {rc_all_pages}")
          return all_pages, rc_all_pages, confluence_obj
     
     def _compare_scores_by_kernel(self, df: pd.DataFrame, type_test: str, stat_rc_vers: str, score=None):
          self.info_for_log['rc_version'] = stat_rc_vers
          comparison_separate_kernel_line_graph_saver = SaveGraph(main_folder=self.stat_title, stat_rc_vers=stat_rc_vers)
          if self.comparison_kernel_list and type_test in self.comparison_kernel_list:
                    separate_kernel = TableSeparatelyByKernel(dataframe=df, score=score)
                    separate_kernel_data = separate_kernel.build(**self.info_for_log)
                    comparison_separate_kernel_line_graph = ComparisonKernelLineGraph(separate_by_kernel_data=separate_kernel_data,
                                                                                      type_test=TypeTest.get_full_name_test_without_df(type_test),
                                                                                      saver=comparison_separate_kernel_line_graph_saver)
                    comparison_separate_kernel_line_graph.draw(**self.info_for_log)
                    main_logger.info(f"Отработало  сравнение по ядрам {self.stat_title} - {type_test} (Заданное при вызове класса) {','.join(self.comparison_kernel_list)}")
     
     def _compare_scores(self, dct_wttaidf: dict, stat_rc_vers: str, score_columns: list = ["Рейтинг"]):
          self.info_for_log['rc_version'] = stat_rc_vers
          if self.comparison_list:
               for comporison_item in self.comparison_list:
                    df1 = dct_wttaidf.get(comporison_item[0])
                    df2 = dct_wttaidf.get(comporison_item[1])
                    condition = (isinstance(df1, pd.DataFrame) and isinstance(df2, pd.DataFrame)) and (not df1.empty and not df2.empty)
                    if condition:
                         for ind, score_col in enumerate(score_columns):
                              sum_table = SummaryTableNew(dataframes=[df1, df2], score=score_col)
                              result_data = sum_table.build(**self.info_for_log)
                              saver_comparison_graph = SaveGraph(main_folder=self.stat_title.replace("/", "-"), stat_rc_vers=stat_rc_vers)
                              sum_graph = SummaryGraph(saver=saver_comparison_graph,
                                                       stand_grade=result_data[0],
                                                       comparison_scale_of_score=result_data[2],
                                                       scale_txt=result_data[1],
                                                       comparison_names=[comporison_item[0], comporison_item[1]],
                                                       graph_name=" vs ".join([comporison_item[0], comporison_item[1]]))
                              sum_graph.draw(graph_ind=ind, y_label=score_col, **self.info_for_log)
                              main_logger.info(f"Отработало сравнение {self.stat_title} (Заданное при вызове класса)")

     def _upload_to_confluence(self, stat_rc_vers: str, pp_title: str):
          uploader = BaseUploader(username=self.username, 
                                  token=self.tokenconf, 
                                  statistics_type=self.stat_title.replace("/", "-"),
                                  stat_rc_vers=stat_rc_vers,
                                  pp_title=pp_title)
          uploader.collect_a_single_html()
          try:
               uploader.upload_page()
          except Exception as err:
               main_logger.exception("Ошибка")
               main_logger.critical(f"{self.stat_title} СТАТИСТИКА НЕ ВЫЛОЖИЛАСЬ!!!!")
     
     def unique_functionality(self, type_test, data_for_tables, rc_version) -> tuple:
          self.info_for_log['rc_version'] = rc_version
          self.info_for_log['type_test'] = type_test
          main_logger.info("Начало уникального функционала для каждого типа статистики")
          saver = SaveTableToFile(main_folder=self.stat_title, stat_rc_vers=rc_version)
          saver_graph = SaveGraph(main_folder=self.stat_title, stat_rc_vers=rc_version)
          table = MainTable(data=data_for_tables.get(type_test), saver=saver)
          df = table.build(**self.info_for_log)
          if isinstance(df, pd.DataFrame) and not df.empty:
               math_table = MathTable(dataframe=df, saver=saver)
               math_table.build(**self.info_for_log)

               reversed_df = df.iloc[::-1]
               graph = MainGraph(list_of_score=reversed_df.iloc[:, -1], 
                                   scale_txt=Scale.get_base_scale_text(dataframe=reversed_df), 
                                   type_test=TypeTest.get_type_test(dataframe=df),
                                   saver=saver_graph)
               graph.draw(**self.info_for_log)
               main_logger.info("Конец уникального функционала для каждого типа статистики")
               return True, df
          else:
               main_logger.info("Конец уникального функционала для каждого типа статистики")
               return False, None
     
     @task_logger(level=2)
     def _create_single_stat(self, all_pages, confluence_obj, stat_rc_version=None, pp_title_rc_vers=None, *args, **kwargs):
          self.info_for_log['rc_version'] = stat_rc_version
          parse = MainParser(pages_ids=all_pages, CP=confluence_obj.CP, parser=self.score_parser)
          data_for_tables, d_keys = parse.find_data()

          dct_with_type_test_and_its_df = {}

          for type_test in self.set_of_test_types:
               """
                    TODO Добавить логирование
               """
               flag, df = self.unique_functionality(type_test=type_test, data_for_tables=data_for_tables, rc_version=stat_rc_version)
               """"""
               # Здесь сравнение по ядрам
               if flag:
                    dct_with_type_test_and_its_df[type_test] = df
                    self._compare_scores_by_kernel(df=df,
                                                   type_test=type_test,
                                                   stat_rc_vers=stat_rc_version)

          # Здесь сравнение
          self._compare_scores(dct_wttaidf=dct_with_type_test_and_its_df,
                               stat_rc_vers=stat_rc_version)
          
          try:
               saver_bugs_table = SaveTableToFile(main_folder=self.stat_title, stat_rc_vers=stat_rc_version)
               bugs_table = BugsTable(saver=saver_bugs_table, component=self.stat_title)
               bugs_table.build(**self.info_for_log)
          except NoBugsFoundForComponent:
               main_logger.info(f"Не найдено багов для компонента {self.stat_title}")
          try:
               saver_annotations = SaveText(main_folder=self.stat_title, stat_rc_vers=stat_rc_version)
               annotations = Annotations(saver=saver_annotations, component=self.stat_title)
               annotations.build(**self.info_for_log)
          except NoAnnotationsForComponent:
               main_logger.info(f"Не найдено аннотации для компонента {self.stat_title}")
          # Здесь выкладывание в confluence
          # self._upload_to_confluence(stat_rc_vers=stat_rc_version, pp_title=pp_title_rc_vers)

     @task_logger(level=1)
     def create(self, *args, **kwargs):
          all_pages, rc_all_pages, confluence_obj = self._get_pages()
          if not all_pages or not rc_all_pages:
               main_logger.critical(f"{self.stat_title} НЕ НАЙДЕНЫ НЕОБХОДИМЫЕ СТРАНИЦЫ")
          self._create_single_stat(all_pages=all_pages, confluence_obj=confluence_obj, **self.info_for_log)
          for key_version, pages_ids in rc_all_pages.items():
               stat_for_rc_version_os = key_version.split(" ⬝ ")[1]
               if pages_ids:
                    self.info_for_log['rc_version'] = stat_for_rc_version_os
                    self._create_single_stat(all_pages=pages_ids,
                                             confluence_obj=confluence_obj,
                                             stat_rc_version=stat_for_rc_version_os,
                                             pp_title_rc_vers=key_version,
                                             **self.info_for_log)
                    
     def __del__(self):
          shutil.rmtree(self.stat_title.replace("/", "-"))
          main_logger.debug(f"Удаляем директорию {self.stat_title.replace("/", "-")}")


class InheritedStatistics(BaseStatistics):
     pass
     """  
          1) Pages
          2) Parser
          3) Table
          4) Graphs
          5) Uploader
     """


class PostgreSQLStatistics(BaseStatistics):
     def __init__(self, stat_title, username, tokenconf, set_of_test_types: set, comparison_list: list = None, comparison_kernel_list: list = None, score_parser = PostgreSQLParser):
          super().__init__(stat_title, username, tokenconf, set_of_test_types, comparison_list, comparison_kernel_list, score_parser)
          main_logger.info(f"Отработал конструктор {self.__class__.__name__}, {self.stat_title}")
     
     def unique_functionality(self, type_test, data_for_tables, rc_version) -> tuple:
          self.info_for_log['rc_version'] = rc_version
          balance_columns_score = ["Number of failed queries", "Percent of failed queries"]
          columns = ["Релиз", "Ядро", "Режим защищенности", "Стенд"]
          saver = SaveTableToFile(main_folder=self.stat_title, stat_rc_vers=rc_version)
          if not type_test == "psql balance":
               return super().unique_functionality(type_test, data_for_tables, rc_version)
          table = MainTable(data=data_for_tables.get(type_test), saver=saver, columns=columns, columns_scores=balance_columns_score)
          df = table.build(**self.info_for_log)
          if isinstance(df, pd.DataFrame) and not df.empty:
               # Здесь сравнение по ядрам
               for ind, score in enumerate(balance_columns_score):
                    comp_separate_kernel_line_graph_saver = SaveGraph(main_folder=self.stat_title, stat_rc_vers=rc_version)
                    separate_kernel = TableSeparatelyByKernel(dataframe=df, score=score)
                    separate_kernel_data = separate_kernel.build(**self.info_for_log)
                    comparison_separate_kernel_line_graph = ComparisonKernelLineGraph(separate_by_kernel_data=separate_kernel_data, 
                                                                                      type_test=TypeTest.get_full_name_test_without_df(type_test), 
                                                                                      saver=comp_separate_kernel_line_graph_saver)
                    comparison_separate_kernel_line_graph.draw(graph_ind=ind, y_label=balance_columns_score[ind], **self.info_for_log)
               main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return True, df
          else:
               main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return False, None
          # return super().unique_functionality(type_test, data_for_tables, rc_version)


class FreeIpaStatistics(BaseStatistics):
     def __init__(self, stat_title, username, tokenconf, set_of_test_types: set, comparison_list: list = None, comparison_kernel_list: list = None, score_parser = FreeIpaParser):
          super().__init__(stat_title, username, tokenconf, set_of_test_types, comparison_list, comparison_kernel_list, score_parser)
          main_logger.info(f"Отработал конструктор {self.__class__.__name__}, {self.stat_title}")
     
     def unique_functionality(self, type_test, data_for_tables, rc_version) -> tuple:
          main_logger.info(f"Начало уникального функционала для {self.__class__.__name__}")
          self.info_for_log['rc_version'] = rc_version
          saver = SaveTableToFile(main_folder=self.stat_title, stat_rc_vers=rc_version)

          columns = ["Релиз", "Ядро", "Режим защищенности", "Стенд"]
          col_scores = ["Задержка (в сек.) аутен. и авториз. при макс. кол-ве пользователей", 
                        "Число (в %) непройденных аутен. и авториз. в секунду при макс. кол-ве пользователей",
                        "Рейтинг"]

          table = MainTable(data=data_for_tables.get(type_test), saver=saver, columns=columns, columns_scores=col_scores)
          df = table.build(**self.info_for_log)
          if isinstance(df, pd.DataFrame) and not df.empty:
               # Здесь сравнение по ядрам
               for ind, score in enumerate(col_scores):
                    comp_separate_kernel_line_graph_saver = SaveGraph(main_folder=self.stat_title, stat_rc_vers=rc_version)
                    separate_kernel = TableSeparatelyByKernel(dataframe=df, score=score, stat_title=self.stat_title, rc_version=rc_version)
                    separate_kernel_data = separate_kernel.build(**self.info_for_log)
                    comparison_separate_kernel_line_graph = ComparisonKernelLineGraph(separate_by_kernel_data=separate_kernel_data, 
                                                                                      type_test=TypeTest.get_full_name_test_without_df(type_test), 
                                                                                      saver=comp_separate_kernel_line_graph_saver)
                    comparison_separate_kernel_line_graph.draw(graph_ind=ind, y_label=col_scores[ind], **self.info_for_log)
               main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return True, df
          else:
               main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return False, None
                         

class VirtStatistics(BaseStatistics):
     def __init__(self, stat_title, username, tokenconf, set_of_test_types: set, comparison_list: list = None, comparison_kernel_list: list = None, score_parser = VirtParser):
          super().__init__(stat_title, username, tokenconf, set_of_test_types, comparison_list, comparison_kernel_list, score_parser)
          self.score_columns = {
               "FIO": ["1 VM iops write", "70 VM iops write", "1 VM iops read", "70 VM iops read",
                       "1 VM latency-avg write", "70 VM latency-avg write", "1 VM latency-avg read", "70 VM latency-avg read"],
               "vPingPong": ["Рейтинг"],
               "vUnixBench": ["Рейтинг 4 ядер", "Рейтинг 8 ядер", "Рейтинг 12 ядер"],
               "steal time": ["1 VM mean instructions", "70 VM mean instuctions", "1 VM mean steal time", "70 VM mean steal time"],
               "steal time-sm": ["1 VM mean instructions", "70 VM mean instuctions", "1 VM mean steal time", "70 VM mean steal time"]
          }
          main_logger.info(f"Отработал конструктор {self.__class__.__name__}, {self.stat_title}")
     
     def _compare_scores(self, dct_wttaidf: dict, stat_rc_vers: str, score_column: list = ["Рейтинг"]):
          return super()._compare_scores(dct_wttaidf, stat_rc_vers, ["70 VM mean instuctions", "70 VM mean steal time"])

     def unique_functionality(self, type_test, data_for_tables, rc_version) -> tuple:
          self.info_for_log['rc_version'] = rc_version
          main_logger.info(f"Начало уникального функционала для {self.__class__.__name__}")
          saver = SaveTableToFile(main_folder=self.stat_title.replace("/", "-"), stat_rc_vers=rc_version)
          columns = ["Релиз", "Ядро", "Режим защищенности", "Стенд"]
          try:
               score_cols = self.score_columns[type_test]
          except KeyError:
               score_cols = ["Рейтинг"]
          table = MainTable(data=data_for_tables.get(type_test), 
                            saver=saver,
                            columns=columns,
                            columns_scores=score_cols)
          df = table.build(**self.info_for_log)
          if isinstance(df, pd.DataFrame) and not df.empty:
               comparison_separate_kernel_line_graph_saver = SaveGraph(main_folder=self.stat_title.replace("/", "-"), stat_rc_vers=rc_version)
               for ind, item in enumerate(score_cols):
                    separate_kernel = TableSeparatelyByKernel(dataframe=df, score=item)
                    separate_kernel_data = separate_kernel.build(**self.info_for_log)
                    comparison_separate_kernel_line_graph = ComparisonKernelLineGraph(separate_by_kernel_data=separate_kernel_data, 
                                                                                      type_test=TypeTest.get_full_name_test_without_df(type_test), 
                                                                                      saver=comparison_separate_kernel_line_graph_saver)
                    comparison_separate_kernel_line_graph.draw(graph_ind=ind, y_label=item, y_lim=True, **self.info_for_log)
               main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return True, df
          else:
               main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return False, None
          

class ParsecStatistics(BaseStatistics):
     def __init__(self, stat_title, username, tokenconf, set_of_test_types: set, comparison_list: list = None, comparison_kernel_list: list = None, score_parser = ParsecParser):
          super().__init__(stat_title, username, tokenconf, set_of_test_types, comparison_list, comparison_kernel_list, score_parser)
          self.score_columns = {
               "parsec impact-fs": ["Total used by parsec func in %"],
               "parsec impact-fs aud-off": ["Total used by parsec func in %"],
               "digsig-cdt": ["Подписано", "Неподписано"]
          }
          main_logger.info(f"Отработал конструктор {self.__class__.__name__}, {self.stat_title}")
     
     def _compare_scores(self, dct_wttaidf: dict, stat_rc_vers: str, score_column: str = ["Рейтинг"]):
          return super()._compare_scores(dct_wttaidf, stat_rc_vers, ["Total used by parsec func in %"])
     
     def unique_functionality(self, type_test, data_for_tables, rc_version) -> tuple:
          self.info_for_log['rc_version'] = rc_version
          main_logger.info(f"Начало уникального функционала для {self.__class__.__name__}")
          saver = SaveTableToFile(main_folder=self.stat_title.replace("/", "-"), stat_rc_vers=rc_version)
          columns = ["Релиз", "Ядро", "Режим защищенности", "Стенд"]
          try:
               score_cols = self.score_columns[type_test]
          except KeyError:
               score_cols = ["Рейтинг"]
          table = MainTable(data=data_for_tables.get(type_test), 
                            saver=saver,
                            columns=columns,
                            columns_scores=score_cols)
          df = table.build(**self.info_for_log)
          if isinstance(df, pd.DataFrame) and not df.empty:
               comparison_separate_kernel_line_graph_saver = SaveGraph(main_folder=self.stat_title.replace("/", "-"), stat_rc_vers=rc_version)
               for ind, item in enumerate(score_cols):
                    separate_kernel = TableSeparatelyByKernel(dataframe=df, score=item)
                    separate_kernel_data = separate_kernel.build(**self.info_for_log)
                    comparison_separate_kernel_line_graph = ComparisonKernelLineGraph(separate_by_kernel_data=separate_kernel_data, 
                                                                                      type_test=TypeTest.get_full_name_test_without_df(type_test), 
                                                                                      saver=comparison_separate_kernel_line_graph_saver)
                    comparison_separate_kernel_line_graph.draw(graph_ind=ind, y_label=item, **self.info_for_log)
               main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return True, df
          else:
               main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return False, None
          

class DockerStatstics(BaseStatistics):
     def __init__(self, stat_title, username, tokenconf, set_of_test_types: set, comparison_list: list = None, comparison_kernel_list: list = None, score_parser = DockerParser):
          super().__init__(stat_title, username, tokenconf, set_of_test_types, comparison_list, comparison_kernel_list, score_parser)
          self.score_columns = {
               "docker-wa": ["Приложение в Docker | Нагрузчик в Docker", "Приложение в Docker | Нагрузчик на хосте", "Приложение на хосте | Нагрузчик на хосте"],
          }
          main_logger.info(f"Отработал конструктор {self.__class__.__name__}, {self.stat_title}")
     
     def unique_functionality(self, type_test, data_for_tables, rc_version) -> tuple:
          self.info_for_log['rc_version'] = rc_version
          main_logger.info(f"Начало уникального функционала для {self.__class__.__name__}")
          saver = SaveTableToFile(main_folder=self.stat_title.replace("/", "-"), stat_rc_vers=rc_version)
          columns = ["Релиз", "Ядро", "Режим защищенности", "Стенд"]
          try:
               score_cols = self.score_columns[type_test]
          except KeyError:
               score_cols = ["Рейтинг"]
          table = MainTable(data=data_for_tables.get(type_test), 
                            saver=saver,
                            columns=columns,
                            columns_scores=score_cols)
          df = table.build(**self.info_for_log)
          if isinstance(df, pd.DataFrame) and not df.empty:
               if type_test == "docker-wa":
                    # main_graph_docker_saver = SaveInteractiveGraph(main_folder=self.stat_title.replace("/", "-"), stat_rc_vers=rc_version)
                    # main_group_inter_graph = MainGroupInteractiveGraph(dataframe=df,
                    #                                                    type_test=type_test,
                    #                                                    score_columns=score_cols,
                    #                                                    saver=main_graph_docker_saver)
                    # main_group_inter_graph.draw()
                    separate_kernel = TableSeparatelyByKernel(dataframe=df, score=score_cols)
                    separate_kernel_data = separate_kernel.build(**self.info_for_log)
                    saver_sum_line_graph = SaveGraph(main_folder=self.stat_title.replace("/", "-"), stat_rc_vers=rc_version)
                    for ind, (kernel, df_sep_by_kernel) in enumerate(separate_kernel_data.items(), start=1):
                         ratings_for_graphs = []
                         for score_col in score_cols:
                              rat_tmp = df_sep_by_kernel[score_col].tolist()
                              ratings_for_graphs.append(rat_tmp)

                         line_graph_one_kernel = SummaryLineGraph(
                              saver=saver_sum_line_graph,
                              stand_grade=df_sep_by_kernel["Стенд"].mode()[0],
                              comparison_scale_of_score=ratings_for_graphs,
                              scale_txt=df_sep_by_kernel['Релиз'],
                              comparison_names=score_cols,
                              graph_name=f"{kernel}",
                              colors=['#f90829', '#007b7a', '#f9b312']
                         )
                         line_graph_one_kernel.draw(graph_ind=ind, **self.info_for_log)
                    main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return True, df
          else:
               main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return False, None
          

class FileSystemStatistics(BaseStatistics):
     def __init__(self, stat_title, username, tokenconf, set_of_test_types: set, comparison_list: list = None, comparison_kernel_list: list = None, score_parser = FileSystemParser):
          super().__init__(stat_title, username, tokenconf, set_of_test_types, comparison_list, comparison_kernel_list, score_parser)

     def unique_functionality(self, type_test, data_for_tables, rc_version) -> tuple:
          self.info_for_log['rc_version'] = rc_version
          ceph_fio_columns_score = ["iops write", "iops read", "latency-avg write", "latency-avg read"]
          columns = ["Релиз", "Ядро", "Режим защищенности", "Стенд"]
          saver = SaveTableToFile(main_folder=self.stat_title, stat_rc_vers=rc_version)
          if not type_test == "CEPH fio":
               return super().unique_functionality(type_test, data_for_tables, rc_version)
          table = MainTable(data=data_for_tables.get(type_test), saver=saver, columns=columns, columns_scores=ceph_fio_columns_score)
          df = table.build(**self.info_for_log)
          if isinstance(df, pd.DataFrame) and not df.empty:
               for ind, score in enumerate(ceph_fio_columns_score):
                    comp_separate_kernel_line_graph_saver = SaveGraph(main_folder=self.stat_title, stat_rc_vers=rc_version)
                    separate_kernel = TableSeparatelyByKernel(dataframe=df, score=score)
                    separate_kernel_data = separate_kernel.build(**self.info_for_log)
                    comparison_separate_kernel_line_graph = ComparisonKernelLineGraph(separate_by_kernel_data=separate_kernel_data, 
                                                                                      type_test=TypeTest.get_full_name_test_without_df(type_test), 
                                                                                      saver=comp_separate_kernel_line_graph_saver)
                    comparison_separate_kernel_line_graph.draw(graph_ind=ind, y_label=ceph_fio_columns_score[ind], **self.info_for_log)
               main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return True, df
          else:
               main_logger.info(f"Конец уникального функционала для {self.__class__.__name__}")
               return False, None
          
     