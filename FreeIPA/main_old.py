from pages import Pages
from parsers import MainParser
# from logging_conf import main_logger
from parser_table import parsing_table_with_results
from confluence.confluence_conf import ID_ROOT_PAGES, CONFLUENCE_SPACE
from confluence.confluence import UpdatePageReportToConfluence
from recalculation import Report
from replace_total_rating import replace_total_rating_in_html

def get_pages(username, tokenconf, password, stat_title):
    confluence_obj = Pages(username=username, token=tokenconf, password=password)
    all_pages, rc_all_pages = [], {}
    for id_root_page in ID_ROOT_PAGES:
        pages_major_update, rc_pages_major_update = confluence_obj.get_list_pages(id_root_page=id_root_page, 
                                                                                    stat_title=stat_title)
        all_pages.extend(pages_major_update)
        rc_all_pages.update(rc_pages_major_update)
    # main_logger.debug(f"ID всех страниц - {all_pages};\nID всех страниц RC и имена их родителей - {rc_all_pages}")
    return all_pages, rc_all_pages, confluence_obj

all_pages, rc_all_pages, confluence_obj = get_pages(username=..., 
                                                    password=...,
                                                    tokenconf=..., 
                                                    stat_title="FreeIPA")
# print(all_pages)

parser = MainParser(pages_ids=all_pages, CP=confluence_obj.CP)
titles = parser.get_list_pages_with_report()
# for title in titles:
#     test_page_soup, test_page_data = parser.basic_page_parsing(title)
#     # print(titles)
#     # print(len(titles))
#     # for ind, title in enumerate(titles):
#     #     print(ind, title)
#     print(title)
#     df = parsing_table_with_results(soup=test_page_soup)
#     print(df)
#     report = Report(dataframe=df)
#     tr = report.get_total_rating()
#     print(tr)
if titles:
    for title in titles:
        test_page_soup, test_page_data = parser.basic_page_parsing(title)
        # print(test_page_soup)
        df = parsing_table_with_results(soup=test_page_soup)
        report = Report(dataframe=df)
        tr = report.get_total_rating()

        new_html = replace_total_rating_in_html(html_text=test_page_soup, new_total_rating=tr)
        # print(new_html)
        print(test_page_data)
        new_page = UpdatePageReportToConfluence(username=...,
                                                password=...)
        new_page.update_confluence_page(page_space=CONFLUENCE_SPACE,
                                        page_title=title,
                                        page_body=new_html)