import re
import pandas
import numpy as np

from libreport import ConfluencePage
from pretty_html_table import build_table
from bs4 import BeautifulSoup


def get_statistics(statistical_sampling_lst,
                   sampling_name='nameless',
                   save_to_html=True,
                   save_to_csv=True,
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

    if save_to_csv:
        raw_stat_table.to_csv('{}_table.csv'.format(sampling_name))

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