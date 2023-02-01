# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import re
import pandas
import argparse
import numpy as np

from libreport import ConfluencePage
from pretty_html_table import build_table
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


def get_statistics(statistical_sampling_lst,
                   sampling_name='nameless',
                   save_to_html=True,
                   output_to_console=True,
                   accuracy=2) -> dict:
    """
        :statistical_sampling_lst: список выборки
        :sampling_name: название выборки
        :save_to_html: True/False сохранение в виде html
        :output_to_console: True/False вывод на консоль
        :return: словарь основных статистик
    """

    print('\033[92m++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++\033[0m')

    statistics = {
        'min': min(statistical_sampling_lst),
        'max': max(statistical_sampling_lst),
        'mean': round(np.mean(statistical_sampling_lst), accuracy),
        'median': round(np.median(statistical_sampling_lst), accuracy),
        'std': round(np.std(statistical_sampling_lst), accuracy),
        'var': round(np.var(statistical_sampling_lst), accuracy),
        'smin': round(1 - min(statistical_sampling_lst) / np.mean(statistical_sampling_lst), accuracy),
        'smax': round(max(statistical_sampling_lst) / np.mean(statistical_sampling_lst) - 1, accuracy),
    }

    # raw_stat_table = pandas.DataFrame(statistics, index=[0])
    # print(raw_stat_table)
    raw_stat_table = pandas.DataFrame({'Оценка': ('MIN',
                                                  'MAX',
                                                  'Мат. ожидание',
                                                  'Медиана',
                                                  'Стандартное отклонение',
                                                  'Дисперсия',
                                                  'Отклонение MIN от среднего',
                                                  'Отклонение MAX от среднего'),
                                       sampling_name: (statistics['min'],
                                                       statistics['max'],
                                                       statistics['mean'],
                                                       statistics['median'],
                                                       statistics['std'],
                                                       statistics['var'],
                                                       statistics['smin'],
                                                       statistics['smax'])
                                       })
    if save_to_html:
        beauty_table = build_table(raw_stat_table, 'blue_light')
        with open('{}_table.html'.format(sampling_name), 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)

    if output_to_console:
        print('{} MIN: {}'.format(sampling_name, str(statistics['min'])))
        print('{} MAX: {}'.format(sampling_name, str(statistics['max'])))
        print('{} Мат. ожидание: {}'.format(sampling_name, str(statistics['mean'])))
        print('{} Медиана: {}'.format(sampling_name, str(statistics['median'])))
        print('{} Стандартное отклонение: {}'.format(sampling_name, str(statistics['std'])))
        print('{} Дисперсия: {}'.format(sampling_name, str(statistics['var'])))
        print('{} Отклонение MIN от среднего: {}'.format(sampling_name, str(statistics['smin'])))
        print('{} Отклонение MAX от среднего: {}'.format(sampling_name, str(statistics['smax'])))

    return statistics, raw_stat_table


def get_all_pages_as_html(pattern,
                          space,
                          verbose=False) -> list:
    """
        :pattern: начальное слово идентификатор в названии страницы
        :space: имя пространства Confluence
        :return: список обЪектов типа BeautifulSoup
    """
    # список всех возможных имен страниц
    possible_page_names = ['{}_{}_{}_{}_{}_{}'.format(pattern, version, astra_mode, kernel, grid, inv_num)
                           for version in ('1.7.1', '1.7.2', '1.7.3', '1.7.3.UU.1')
                           for astra_mode in ('orel', 'voronezh', 'smolensk')
                           for kernel in ('5.10.0-1045-generic', '5.10.0-1057-generic', '5.15.0-33-generic', '5.15.0-33-lowlatency',)
                           for grid in ('low', 'middle', 'high')
                           for inv_num in ('129', '141', '150', '151')
                           ]

    # список имен страниц, которые реально существуют
    cp = ConfluencePage(username=args.USER, token=args.TOKEN)
    existing_names = [name for name in possible_page_names if cp.page_exists(space, name)]
    if verbose:
        print(existing_names)

    # список обЪектов soup
    soups_src_htmls = [BeautifulSoup(cp.get_page_as_html(space, name), 'lxml') for name in existing_names]

    return soups_src_htmls


def get_ratings_from_soups(soups_src_htmls,
                           verbose=False) -> list:
    """
        :soups_src_htmls: список обЪектов типа BeautifulSoup
        :verbose: True/False промежуточный вывод листа с рейтингами
        :return: лист с рейтингами типа float
    """
    ratings = []

    # обойти все soop найти рейтинги
    for soup_src_html in soups_src_htmls:
        tags = soup_src_html.find_all(name='h2')
        for tag in tags:
            rating = re.search(r'rating:\s?(\d+.\d+)', tag.text)
            if rating is not None:
                ratings.append(float(rating.group(1)))
    if verbose:
        print(ratings)
    return ratings


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
        statistics = get_statistics(ratings,
                                    args.PATTERN,
                                    args.SAVE_TO_HTML,
                                    args.OUTPUT_TO_CONSOLE)
    else:
        exit(2)


