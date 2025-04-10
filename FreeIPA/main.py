from pages import Pages
from parsers import MainParser
from recalculation import Report
from argparse import ArgumentParser
from parser_table import parsing_table_with_results
from replace_total_rating import replace_total_rating_in_html
from confluence.confluence import UpdatePageReportToConfluence
from confluence.confluence_conf import ID_ROOT_PAGES, CONFLUENCE_SPACE

parser = ArgumentParser()
parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

parser.add_argument('-pswd', '--password',
                    action='store',
                    required=False,
                    default=None,
                    help='confluence password',
                    dest='PASSWORD')

parser.add_argument('-t', '--token',
                    action='store',
                    required=False,
                    default=None,
                    help='confluence access token',
                    dest='TOKEN')

args = parser.parse_args()

class ConfluenceReportUpdater:
    def __init__(self, username, password, tokenconf, stat_title="FreeIPA"):
        self.username = username
        self.password = password
        self.tokenconf = tokenconf
        self.stat_title = stat_title
        self.confluence_obj = Pages(username=username, token=tokenconf, password=password)
        self.get_pages()

    def get_pages(self):
        self.all_pages, self.rc_all_pages = [], {}
        for id_root_page in ID_ROOT_PAGES:
            pages_major_update, rc_pages_major_update = self.confluence_obj.get_list_pages(id_root_page=id_root_page, stat_title=self.stat_title)
            self.all_pages.extend(pages_major_update)
            self.rc_all_pages.update(rc_pages_major_update)
        return self.all_pages, self.rc_all_pages
    
    def parse_and_update_pages(self, pages):
        if not pages:
            raise Exception("Старницы не найдены")
        parser = MainParser(pages_ids=pages, CP=self.confluence_obj.CP)
        titles = parser.get_list_pages_with_report()
        
        if not titles:
            return
            
        for title in titles:
            test_page_soup, test_page_data = parser.basic_page_parsing(title)
            df = parsing_table_with_results(soup=test_page_soup)
            report = Report(dataframe=df)
            total_rating = report.get_total_rating()
            new_html = replace_total_rating_in_html(html_text=test_page_soup, new_total_rating=total_rating)
            updater = UpdatePageReportToConfluence(username=self.username, password=self.password)
            updater.update_confluence_page(page_space=CONFLUENCE_SPACE, page_title=title, page_body=new_html)

    def process_all_pages(self):
        self.parse_and_update_pages(self.all_pages)
        for key_version, pages_ids in self.rc_all_pages.items():
            self.parse_and_update_pages(pages_ids)

if __name__ == "__main__":
    """TODO токен времено отключил, только по паролю из EKA"""
    updater = ConfluenceReportUpdater(
        username=args.USER,
        password=args.PASSWORD,
        tokenconf=args.TOKEN,
        stat_title="FreeIPA"
    )
    updater.process_all_pages()