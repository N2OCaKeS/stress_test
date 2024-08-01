import re
from abc import abstractmethod
from bs4 import BeautifulSoup

from confluence.confluence import ConfluencePage
from confluence.confluence_conf import CONFLUENCE_SPACE


class MainParser:
    def __init__(self, pages_ids, CP, parser):
        self.pages_ids = pages_ids
        self.CP = CP
        self.parser = parser
    
    def __get_list_pages_with_report(self):
        titles = []
        for id_page in self.pages_ids:
            titles.extend(self.CP.get_child_page_as_html(id_page))
        return titles
    
    def __basic_page_parsing(self, title):
        """
            Получаем конкретную страницу отчета
        """
        src_html = self.CP.get_page_as_html(page_space=CONFLUENCE_SPACE, page_title=title)
        data = src_html.get("body").get("view").get("value")
        soup = BeautifulSoup(data, 'lxml')
        title_data = title.replace(" ", "_").split("_")
        set_titles = {"parsec", "vanilla", "balance", "impact-fs", "auth", "time"}
        if title_data[1] == "impact-fs" and title_data[2] == "aud-off":
            parsec_or_the_rest = None
            type_test, astra_version, sec_mode, kernel, stand = f"{title_data[0]}_{title_data[1]}-{title_data[2]}", title_data[3], title_data[4], title_data[5], title_data[6]
        elif title_data[1] in set_titles:
            type_test, parsec_or_the_rest, astra_version, sec_mode, kernel, stand = title_data[0], title_data[1], title_data[2], title_data[3], title_data[4], title_data[5]
        else:
            parsec_or_the_rest = None
            type_test, astra_version, sec_mode, kernel, stand = title_data[0], title_data[1], title_data[2], title_data[3], title_data[4]

        if parsec_or_the_rest:
            type_test = f"{type_test}_{parsec_or_the_rest}"

        title_data_dict = {
            "type_test": type_test,
            "astra_version": astra_version,
            "kernel": kernel,
            "sec_mode": sec_mode,
            "stand": stand
        }

        return (soup, title_data_dict)

    def find_data(self) -> dict:
        data = {}
        for title in self.__get_list_pages_with_report():
            html_page, dict_with_title_data = self.__basic_page_parsing(title)
            score_parser = self.parser()
            score = score_parser.find_score(html_page=html_page, type_test=dict_with_title_data.get("type_test"))
            dict_with_title_data['score'] = score
            data.setdefault(dict_with_title_data.get("type_test"), [])
            data[dict_with_title_data.get("type_test")].append(dict_with_title_data)
        return data, data.keys()


class ScoreParser:
    @abstractmethod
    def find_score() -> tuple:
        pass

"""
    Имлпементация необходимых парсеров
"""
class BaseParser(ScoreParser):
    def find_score(self, html_page, re_template: str = "[Tt]otal rating", type_test=None, ind=2) -> tuple: 
        """
            Выдергиваем значение рейтинга из html страницы
        """
        try:
            rating = html_page.find(string=re.compile(re_template)).strip().split(" ")[ind]
        except:
            rating = 0
        return (rating,)
    

class OneRowTwoCollTableParser(ScoreParser):
    def find_score(self, html_page, re_template: str = None, type_test=None) -> tuple:
        try:
            value = html_page.find(string=re_template).next_element.next_element.text
            value = float(value)
        except:
            value = 0
        return (value,)

class ApacheParser(OneRowTwoCollTableParser):
    def find_score(self, html_page, re_template: str = "Requests per second", type_test=None) -> tuple:
        rps = super().find_score(html_page=html_page, re_template=re_template)
        return rps
    

class ParsecParser(OneRowTwoCollTableParser):
    def find_score(self, html_page, re_template: str = "Total used by parsec func", type_test=None) -> tuple:
        total_used_by_parsec_func = super().find_score(html_page=html_page, re_template=re_template)
        return total_used_by_parsec_func


class FreeIpaParser(BaseParser):
    def find_score(self, html_page, type_test=None) -> tuple:
        rating = super().find_score(html_page)[0]
        data_table = []
        try:
            table = html_page.find_all("table")[1]
            # пройдемся по всем строкам таблицы, кроме заголовка
            for row in table.find_all('tr')[1:]:
                cols = row.find_all('td')  # найдем все столбцы
                cols = [col.text.strip() for col in cols]  # очистим от лишних пробелов
                data_table.append(cols)  # добавим в итоговый список
        except:
            pass
        
        try:
            sr_znach_max_users = data_table[-1][2]
            proc_errors_max_users = data_table[-1][1]
        except IndexError:
            sr_znach_max_users = None
            proc_errors_max_users = None

        return (sr_znach_max_users, proc_errors_max_users, rating)


class VirtParser(BaseParser):
    def vpp_find_score(self, html_page) -> tuple:
        vping_pong = super().find_score(html_page=html_page, re_template="Score", ind=1)[0]
        return vping_pong

    def fio_find_score(self, html_page) -> tuple:
        data_table = []
        try:
            table_one = html_page.find_all("table")[1]
            table_many = html_page.find_all("table")[2]
            for table in (table_one, table_many):
                for row in table.find_all("tr")[1:]:
                    cols = row.find_all("td")
                    cols = [col.text.strip() for col in cols]
                    data_table.append(cols)
        except:
            pass
        try:
            one_iops_write = data_table[0][0]
            one_latency_avg_write = data_table[0][1]
            one_iops_read = data_table[1][0]
            one_latency_avg_read = data_table[1][1]

            many_iops_write = data_table[2][0]
            many_latency_avg_write = data_table[2][1]
            many_iops_read = data_table[3][0]
            many_latency_avg_read = data_table[3][1]
        except IndexError:
            one_iops_write, one_latency_avg_write, one_iops_read, one_latency_avg_read = 0,0,0,0
            many_iops_write, many_latency_avg_write, many_iops_read, many_latency_avg_read = 0,0,0,0

        return (one_iops_write, many_iops_write,
                one_iops_read, many_iops_read,
                one_latency_avg_write, many_latency_avg_write,
                one_latency_avg_read, many_latency_avg_read)

    def steal_time_find_score(self, html_page) -> tuple:
        data_table = []
        try:
            table_one_mean_instructions = html_page.find_all("table")[1]
            table_one_mean_steal_time = html_page.find_all("table")[2]
            table_many_mean_instructions = html_page.find_all("table")[5]
            table_many_mean_steal_time = html_page.find_all("table")[6]
            for table in (table_one_mean_instructions, table_one_mean_steal_time, 
                          table_many_mean_instructions, table_many_mean_steal_time):
                for row in table.find_all("tr")[1:]:
                    cols = row.find_all("td")
                    cols = [col.text.strip() for col in cols]
                    data_table.append(cols)
        except:
            pass
        try:
            one_mean_instructions = data_table[0][0]
            one_mean_steal_time = data_table[1][0]
            many_mean_instructions = data_table[2][0]
            many_mean_steal_time = data_table[3][0]
        except IndexError:
            one_mean_instructions, one_mean_steal_time, many_mean_instructions, many_mean_steal_time = 0,0,0,0
        return one_mean_instructions, many_mean_instructions, one_mean_steal_time, many_mean_steal_time
    
    def vunix_bench_find_score(self, html_page) -> tuple:
        data_table = []
        try:
            table = html_page.find_all("table")[1]
            for row in table.find_all("tr")[1:]:
                cols = row.find_all("td")
                cols = [col.text.strip() for col in cols]
                data_table.append(cols)
        except:
            pass
        try:
            parallel_4 = data_table[0][0]
            parallel_8 = data_table[1][0]
            parallel_12 = data_table[2][0]
        except IndexError:
            parallel_4, parallel_8, parallel_12 = 0,0,0
        return parallel_4, parallel_8, parallel_12

    def find_score(self, html_page, type_test=None) -> tuple:
        if type_test == "FIO":
            score = self.fio_find_score(html_page=html_page)
        elif type_test == "steal_time":
            score = self.steal_time_find_score(html_page=html_page)
        elif type_test == "vPingPong":
            score = self.vpp_find_score(html_page=html_page)
        elif type_test == "vUnixBench":
            score = self.vunix_bench_find_score(html_page=html_page)
        else:
            score = (0,)
        return score

