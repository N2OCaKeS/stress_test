# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================
import argparse

from libreport import ConfluencePage
from libstat import get_statistics, get_all_pages_as_html, get_ratings_from_soups
from bs4 import BeautifulSoup

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

parser.add_argument('-t', '--token',
                    action='store',
                    required=False,
                    default=None,
                    help='confluence access token',
                    dest='TOKEN')

parser.add_argument('-c', '--component',
                    action='store',
                    choices=['fs', 'ps', 'sc'],
                    required=False,
                    help='system component',
                    dest='COMPONENT')

parser.add_argument('-pp', '--page-pattern',
                    action='store',
                    choices=['PostgreSQL',
                             'EXT4',
                             'EXT4_parsec',
                             'OCFS2',
                             'OCFS2_parsec',
                             'XFS',
                             'NTFS',
                             'FILEAUD',
                             'PSAUD',
                             'USERAUD',
                             'Syslog-ng'],
                    required=False,
                    help='Page pattern - first letters of the page name. Please, choose from the list',
                    dest='PATTERN')

parser.add_argument('-cs', '--confluence-space',
                    action='store',
                    required=True,
                    help='confluence space name',
                    dest='SPACE')

parser.add_argument('-sth', '--save-to-html',
                    action='store_true',
                    required=False,
                    help='save table in beauty html',
                    dest='SAVE_TO_HTML')

parser.add_argument('-stc', '--save-to-csv',
                    action='store_true',
                    required=False,
                    help='save table in csv',
                    dest='SAVE_TO_CSV')

parser.add_argument('-co', '--console-output',
                    action='store_true',
                    required=False,
                    help='output stat info in console',
                    dest='OUTPUT_TO_CONSOLE')

parser.add_argument('-v', '--verbose',
                    action='store_true',
                    required=False,
                    help='',
                    dest='VERBOSE')


args = parser.parse_args()

if __name__ == '__main__':
    if args.COMPONENT == 'fs':
        cp = ConfluencePage(username=args.USER, token=args.TOKEN)
        src_html = cp.get_page_as_html(args.SPACE, 'Общая статистика. Файловые системы.')
        # print(src_html)

        # ищем все таблицы
        soup = BeautifulSoup(src_html, 'lxml')
        tables = soup.find_all(name='table')

        # записываем в переменные с человеческими названиями
        non_clustered_file_systems_table = tables[0]
        non_clustered_file_systems_stat_table = tables[1]
        clustered_file_systems_table = tables[2]
        clustered_file_systems_stat_table = tables[3]

        ################################################################################################################
        # ищем в первой таблице все значения rating
        ratings = []
        for link in non_clustered_file_systems_table.find_all(name='a'):
            ratings.append(float(link.get_text()))

        # сортируем относительно ФС
        non_clustered_file_systems_ratings = {
            'ratings_ext4': [rating for rating in ratings[0::4]],
            'ratings_xfs': [rating for rating in ratings[1::4]],
            'ratings_ntfs': [rating for rating in ratings[2::4]],
            'ratings_ext4_parsec': [rating for rating in ratings[3::4]],
        }

        # генерим статистику
        statistic_ext4 = get_statistics(non_clustered_file_systems_ratings['ratings_ext4'],
                                        'ext4',
                                        args.SAVE_TO_HTML,
                                        args.OUTPUT_TO_CONSOLE)
        statistic_xfs = get_statistics(non_clustered_file_systems_ratings['ratings_xfs'],
                                       'xfs',
                                       args.SAVE_TO_HTML,
                                       args.OUTPUT_TO_CONSOLE)
        statistic_ntfs = get_statistics(non_clustered_file_systems_ratings['ratings_ntfs'],
                                        'ntfs',
                                        args.SAVE_TO_HTML,
                                        args.OUTPUT_TO_CONSOLE)
        statistic_ext4_parsec = get_statistics(non_clustered_file_systems_ratings['ratings_ext4_parsec'],
                                               'ext4_parsec',
                                               args.SAVE_TO_HTML,
                                               args.OUTPUT_TO_CONSOLE)
        ################################################################################################################
        ratings = []
        for link in clustered_file_systems_table.find_all(name='a'):
            ratings.append(float(link.get_text()))

        # сортируем относительно версии
        non_clustered_file_systems_ratings = {
            'ratings_ocfs2': [rating for rating in ratings[0::2]],
            'ratings_ocfs2_parsec': [rating for rating in ratings[1::2]],
        }

        statistic_ext4 = get_statistics(non_clustered_file_systems_ratings['ratings_ocfs2'],
                                        'ocfs2',
                                        args.SAVE_TO_HTML,
                                        args.OUTPUT_TO_CONSOLE)
        statistic_ext4_parsec = get_statistics(non_clustered_file_systems_ratings['ratings_ocfs2_parsec'],
                                               'ocfs2_parsec',
                                               args.SAVE_TO_HTML,
                                               args.OUTPUT_TO_CONSOLE)
        ################################################################################################################
    elif args.COMPONENT == 'ps':
        cp = ConfluencePage(username=args.USER, token=args.TOKEN)
        src_html = cp.get_page_as_html(args.SPACE, 'Общая статистика. PostgreSQL.')

        # ищем все таблицы
        soup = BeautifulSoup(src_html, 'lxml')
        tables = soup.find_all(name='table')

        # записываем в переменные с человеческими названиями
        postgresql_table = tables[0]
        postgresql_stat_table = tables[1]

        # ищем в таблице все значения rating
        ratings = []
        for link in postgresql_table(name='a'):
            ratings.append(float(link.get_text()))

        statistic_postgresql_11 = get_statistics(ratings,
                                                 'PSQL11',
                                                 args.SAVE_TO_HTML,
                                                 args.OUTPUT_TO_CONSOLE)

    elif args.COMPONENT == 'sc':
        cp = ConfluencePage(username=args.USER, token=args.TOKEN)
        src_html = cp.get_page_as_html(args.SPACE, 'Общая статистика. Системные службы.')

        # ищем все таблицы
        soup = BeautifulSoup(src_html, 'lxml')
        tables = soup.find_all(name='table')

        # записываем в переменные с человеческими названиями
        syslog_table = tables[0]
        syslog_stat_table = tables[1]
        auditd_table = tables[2]
        auditd_stat_table = tables[3]

        ################################################################################################################
        # ищем в первой таблице Syslog-NG все значения rating
        ratings = []
        for link in syslog_table.find_all(name='a'):
            ratings.append(float(link.get_text()))

        statistic_syslog_ng = get_statistics(ratings,
                                             'Syslog-NG',
                                             args.SAVE_TO_HTML,
                                             args.OUTPUT_TO_CONSOLE)
        ################################################################################################################
        # ищем в первой таблице Auditd все значения rating
        ratings = []
        for link in auditd_table.find_all(name='a'):
            ratings.append(float(link.get_text()))

        # сортируем относительно режима
        auditd_ratings = {
            'ratings_fileaud': [rating for rating in ratings[0::3]],
            'ratings_psaud': [rating for rating in ratings[1::3]],
            'ratings_useraud': [rating for rating in ratings[2::3]],
            'ratings_total': [rating for rating in ratings],
        }

        statistic_auditd_fileaud = get_statistics(auditd_ratings['ratings_fileaud'],
                                                  'fileaud',
                                                  args.SAVE_TO_HTML,
                                                  args.OUTPUT_TO_CONSOLE)
        statistic_auditd_psaud = get_statistics(auditd_ratings['ratings_psaud'],
                                                'psaud',
                                                args.SAVE_TO_HTML,
                                                args.OUTPUT_TO_CONSOLE)
        statistic_auditd_useraud = get_statistics(auditd_ratings['ratings_useraud'],
                                                  'useraud',
                                                  args.SAVE_TO_HTML,
                                                  args.OUTPUT_TO_CONSOLE)
        statistic_auditd_total = get_statistics(auditd_ratings['ratings_total'],
                                                'auditd total',
                                                args.SAVE_TO_HTML,
                                                args.OUTPUT_TO_CONSOLE)
    elif args.PATTERN:
        # райтинги по дереву страниц согласно паттерну
        ratings = get_ratings_from_soups(get_all_pages_as_html(args.PATTERN, args.SPACE, args.VERBOSE), args.VERBOSE)

        # получаем статистические значения
        statistics = get_statistics(statistical_sampling_lst=ratings,
                                    sampling_name=args.PATTERN,
                                    save_to_html=args.SAVE_TO_HTML,
                                    save_to_csv=args.SAVE_TO_CSV,
                                    output_to_console=args.OUTPUT_TO_CONSOLE)
    else:
        exit(2)


