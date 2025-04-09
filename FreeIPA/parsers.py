from bs4 import BeautifulSoup

from confluence.confluence import ConfluencePage
from confluence.confluence_conf import CONFLUENCE_SPACE

# from logging_conf import main_logger

class MainParser:
    def __init__(self, pages_ids, CP, parser=None):
        self.pages_ids = pages_ids
        self.CP = CP
        self.parser = parser
    
    def get_list_pages_with_report(self):
        titles = []
        for id_page in self.pages_ids:
            titles.extend(self.CP.get_child_page_as_html(id_page))
        return titles
    
    def basic_page_parsing(self, title):
        """
            Получаем конкретную страницу отчета
        """
        src_html = self.CP.get_page_as_html(page_space=CONFLUENCE_SPACE, page_title=title)
        data = src_html.get("body").get("view").get("value")
        soup = BeautifulSoup(data, 'lxml')

        title_data = title.split("_")
        type_test, astra_version, sec_mode, kernel, stand = title_data[0], title_data[1], title_data[2], title_data[3], title_data[4]

        title_data_dict = {
            "type_test": type_test,
            "astra_version": astra_version,
            "kernel": kernel,
            "sec_mode": sec_mode,
            "stand": stand
        }

        # main_logger.debug("Возвращаем html и title_data_dict")
        return (soup, title_data_dict)
