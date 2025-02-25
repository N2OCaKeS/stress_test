import os
from abc import abstractmethod

from confluence.confluence import StatisticsToConfluence
from confluence.confluence_conf import CONFLUENCE_SPACE, CONFLUENCE_URL, CONFLUENCE_SPACE

from typetest import TypeTest
from utils import UtilForBuildPath
from logging_conf import main_logger


class TemplateImage:
    template = """ 
            <span class="confluence-embedded-file-wrapper confluence-embedded-manual-size">
                <img class="confluence-embedded-image" draggable="false" src="/download/attachments/{page_id}/{img_png}" data-image-src="/download/attachments/{page_id}/{img_png}" data-unresolved-comment-count="0" data-linked-resource-id="{page_id}" data-linked-resource-version="1" data-linked-resource-type="attachment" data-linked-resource-default-alias="{img_png}" data-base-url="https://{CONFLUENCE_URL}" data-linked-resource-content-type="image/png" data-linked-resource-container-id="{page_id}" data-linked-resource-container-version="6" ></img>
            </span>
            <br/>
        """

class Uploader:
    @abstractmethod
    def collect_a_single_html():
        pass


class BaseUploader:
    NAV_START = """
        <nav>
            <h1>Содержание:</h1>
        <ul>
    """
    NAV_END = """
        </ul>
        </nav>
    """
    NAV_ITEM = """
        <li>
            <a href="#id-Статистика.{page_rc_title}{stat_type_without_probel}-{type_stat_header_without_probel}">{type_stat_header}</a>
        </li>
    """
    def __init__(self, username, token, statistics_type, stat_rc_vers="", pp_title=""):
        """
            TODO ДОБАВИТЬ create_confluence_page
        """
        self.username = username
        self.token = token
        self.statistics_type = statistics_type
        self.folder = UtilForBuildPath.build_path(main_folder=self.statistics_type,
                                                  stat_rc_vers=stat_rc_vers)
        self.page_rc_title = stat_rc_vers if stat_rc_vers else ""
        self.pp_title = pp_title
        self.html_page = ""
        self.confluence_stat = StatisticsToConfluence(username=self.username,
                                                      token=self.token)
        main_logger.info("Начало uploader положено (конструктор)")
        
    def _create_page(self):
        if self.page_rc_title and self.pp_title:
            parent_page = self.pp_title
        else:
            parent_page = "Статистика"
        try:
            self.confluence_stat.create_confluence_page(page_space=CONFLUENCE_SPACE, 
                                                        page_title=f"Статистика.{self.page_rc_title} {self.statistics_type.replace("-", "/")}", 
                                                        parent_page_title=parent_page)
        except Exception as err:
            main_logger.exception(f"Страница Статистика.{self.page_rc_title} {self.statistics_type.replace("-", "/")} не создалась")

    def _read_html_file(self, file_name):
        html_file = open(f"{self.folder}/{file_name}")
        table = html_file.read()
        html_file.close()
        return table

    def _find_files(self):
        base_html_file = {}
        end_of_page = {}
        for file in sorted(os.listdir(self.folder)):
            part_header = file.split("_")
            # set_titles = {"parsec", "vanilla", "balance", "impact-fs", "impact-fs-aud-off", "time", "auth", "time-sm"}
            # if part_header[1] in set_titles:
                # type_stat = part_header[0] + "_" + part_header[1]
            # else:
                # type_stat = part_header[0]
            type_stat = part_header[0]

            self.confluence_stat.attache_files(file=f"{self.folder}/{file}",
                                               page_space=CONFLUENCE_SPACE,
                                               page_title=f"Статистика.{self.page_rc_title} {self.statistics_type.replace("-", "/")}")
            if "BugsTable" not in type_stat and "Annotations" not in type_stat:
                base_html_file.setdefault(type_stat, {})
            temp_var = part_header[-1].split(".")[0]
            if file.endswith(".png"):
                base_html_file[type_stat].setdefault(temp_var, [])
                image = TemplateImage.template.format(page_id=self.confluence_stat.get_confluence_page_id(page_space=CONFLUENCE_SPACE, page_title=f"Статистика.{self.page_rc_title} {self.statistics_type.replace("-", "/")}"), img_png=file, CONFLUENCE_URL=CONFLUENCE_URL)
                base_html_file[type_stat][temp_var].append(image)
            elif file.endswith(".html"):
                if "BugsTable" in file:
                    end_of_page["bugs"] = self._read_html_file(file_name=file)
                    continue
                if "Annotations" in file:
                    end_of_page["annotations"] = self._read_html_file(file_name=file)
                    continue
                base_html_file[type_stat][temp_var] =  self._read_html_file(file_name=file)
            if not "BugsTable" in type_stat:
                base_html_file[type_stat]["Header"] = f"<h1 id='{TypeTest.get_full_name_test_without_df(type_stat)}'><b>{TypeTest.get_full_name_test_without_df(type_stat)}</b></h1>"
            
        return {"base_html_file": base_html_file, "end_of_page": end_of_page}
    
    def collect_a_single_html(self):
        self._create_page()
        html_list = []
        nav_lst = []
        fined_files = self._find_files()
        for type_test, html_src_images_and_tables in fined_files.get("base_html_file").items():
            type_test = TypeTest.get_full_name_test_without_df(type_test)
            
            nav_lst.append(self.NAV_ITEM.format(page_rc_title=self.page_rc_title,
                                                stat_type_without_probel=self.statistics_type.replace(" ", "").replace("-", "/"),
                                                type_stat_header_without_probel=type_test.replace(" ", ""),
                                                type_stat_header=type_test))
            main_logger.debug(f"Добавлен NAV_ITEM {type_test} в html")
            html_list.append("<br/><hr/>")
            html_list.append(html_src_images_and_tables.get("Header"))
            main_logger.debug(f'Добавлен Header {html_src_images_and_tables.get("Header")} в html')
            
            comparison_kernel_line_graphs = html_src_images_and_tables.get("ComparisonKernelLineGraph")
            if comparison_kernel_line_graphs is not None:
                for graph in comparison_kernel_line_graphs:
                    html_list.append(graph)
                    main_logger.debug("Добавлен ComparisonKernelLineGraph в html")

            main_graphs = html_src_images_and_tables.get("MainGraph")
            if main_graphs is not None:
                for graph in main_graphs:
                    html_list.append(graph)
                    main_logger.debug("Добавлен MainGraph в html")
            
            html_list.append(f'<h2><a href="https://{CONFLUENCE_URL}/pages/viewpage.action?pageId=192234259">Описание стендов нагрузочного тестирования</a></h2>')
            html_list.append(html_src_images_and_tables.get("MainTable"))
            if html_src_images_and_tables.get("MainTable"):
                main_logger.debug("Добавлена MainTable в html")
            html_list.append("<br/>")
            html_list.append(html_src_images_and_tables.get("MathTable"))
            if html_src_images_and_tables.get("MathTable"):
                main_logger.debug("Добавлено MathTable в html")
            
            summary_graph = html_src_images_and_tables.get("SummaryGraph")
            if summary_graph is not None:
                for graph in summary_graph:
                    html_list.append(graph)
                    main_logger.debug("Добавлен SummaryGraph в html")
        
        nav_items = "".join(nav_lst)
        nav = self.NAV_START + nav_items  + self.NAV_END
        html_list.insert(0, nav)
        
        bugs = fined_files.get("end_of_page").get("bugs")
        annotations = fined_files.get("end_of_page").get("annotations")

        html_list.append(bugs)
        html_list.append(annotations)
        
        main_logger.debug("Сформирован и вставлен в начала NAV")
        html_list = [str(item) for item in html_list if item is not None]
        self.html_page = "".join(html_list)
        main_logger.info("Сгенерирована страница html для публикации")

    def upload_page(self):
        try:
            self.confluence_stat.update_confluence_page(page_space=CONFLUENCE_SPACE,
                                                        page_title=f"Статистика.{self.page_rc_title} {self.statistics_type.replace("-", "/")}",
                                                        page_body=self.html_page)
            main_logger.info(f"Должна была обновиться страница со статистикой - Статистика.{self.page_rc_title} {self.statistics_type.replace("-", "/")}")
        except Exception as err:
            main_logger.error(f"Статистика.{self.page_rc_title} {self.statistics_type.replace("-", "/")} НЕ ОБНОВИЛАСЬ!")
            main_logger.exception(err)