# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================
import argparse
import pandas
from pretty_html_table import build_table
from libstat import get_all_pages_as_html, get_ratings_from_soups, get_statistics, get_ratings

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

args = parser.parse_args()

if args.PATTERN:
    """
        Получаем рейтинги, имена страниц, сопоставляем, рисуем таблицу .html
    """
    ratings_dict = get_ratings(args.USER, args.TOKEN, args.PATTERN, args.SPACE)

    print(ratings_dict)

    rating_column_name = '{} rating'.format(args.PATTERN)
    dataframe_dict = {'release': [],
                      'kernel': [],
                      'astra_mode': [],
                      'grade': [],
                      rating_column_name: [],
                      }
    for page, rating in ratings_dict.items():
        page_params = page.split('_')
        dataframe_dict['release'].append(page_params[1])
        dataframe_dict['kernel'].append(page_params[2])
        dataframe_dict['astra_mode'].append(page_params[3])
        dataframe_dict['grade'].append(page_params[4])
        dataframe_dict[rating_column_name].append(round(rating))

    raw_ratings_table = pandas.DataFrame(dataframe_dict)

    if args.SAVE_TO_HTML:
        beauty_table = build_table(raw_ratings_table, 'blue_light')
        with open('{}_main_table.html'.format(args.PATTERN), 'w') as beauty_html_table:
            beauty_html_table.write(beauty_table)
    if args.SAVE_TO_CSV:
        raw_ratings_table.to_csv('{}_main_table.csv'.format(args.PATTERN))

    # TODO: сделать графики

    """
        Получаем таблицу статистик
    """
    # рейтинги по дереву страниц согласно паттерну
    ratings = get_ratings_from_soups(get_all_pages_as_html(args.USER, args.TOKEN, args.PATTERN, args.SPACE))

    # получаем статистические значения
    statistics = get_statistics(statistical_sampling_lst=ratings,
                                sampling_name=args.PATTERN,
                                save_to_html=args.SAVE_TO_HTML,
                                save_to_csv=args.SAVE_TO_CSV)
    # TODO: сделать графики
    # TODO: смерджить общий отчет и опубликовать
