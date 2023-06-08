import os
import re
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from bs4 import BeautifulSoup
from atlassian import Confluence


class ConfluencePage:
    __url = 'https://life.astralinux.ru'

    def __init__(self, username, password=None, token=None):
        self.__username = username
        self.__password = password
        self.__access_token = token

        if self.__password is not None:
            self.__confluence = Confluence(url=self.__url,
                                           username=self.__username,
                                           password=self.__password)
        elif self.__access_token is not None:
            self.__confluence = Confluence(url=self.__url,
                                           username=self.__username,
                                           token=self.__access_token)

    def get_page_as_html(self,
                         page_space=None,
                         page_title=None, id=None):

        if id:
            html_page = self.__confluence.get_page_by_id(id, expand="body.view")
            return html_page
        if self.__confluence.page_exists(space=page_space, title=page_title):
            html_page = self.__confluence.get_page_by_title(space=page_space, title=page_title, expand="body.view")
            return html_page
        else:
            return None
        
    def get_child_page_as_html(self, id=None, by_title=True):
        if id:
            if by_title:
                html_page = self.__confluence.get_child_title_list(id)
            else:
                html_page = self.__confluence.get_child_id_list(id)
            return html_page
        else:
            return None


class StatisticsToConfluence():
    __url='https://life.astralinux.ru'
    
    def __init__(self, username, password=None, token=None):
        self.__username = username
        self.__password = password
        self.__access_token = token

        if self.__password is not None:
            self.__confluence = Confluence(url=self.__url,
                                           username=self.__username,
                                           password=self.__password)
        elif self.__access_token is not None:
            self.__confluence = Confluence(url=self.__url,
                                           username=self.__username,
                                           token=self.__access_token)
            

    def attache_files(self, file, page_space, page_title):
        self.__confluence.attach_file(filename=file,
                                      page_id=self.__confluence.get_page_id(space=page_space,
                                                                            title=page_title),
                                      title=page_title,
                                      space=page_space)
        
    def get_confluence_page_id(self, page_space, page_title):
        return self.__confluence.get_page_id(space=page_space, title=page_title)


    def get_confluence_public_url(self, page_space, page_title):
        return ('{url}/pages/viewpage.action?pageId={id}#'.format(url=self.__url,
                                                                  id=self.__confluence.get_page_id(space=page_space,
                                                                                                   title=page_title)))
    
    def create_confluence_page(self,
                               page_space,
                               parent_page_title,
                               page_title,
                               page_body='this page was automatically created',):
        if not self.__confluence.page_exists(space=page_space, title=page_title):
            if self.__confluence.create_page(space=page_space,
                                             title=page_title,
                                             body=page_body,
                                             parent_id=self.__confluence.get_page_id(space=page_space,
                                                                                     title=parent_page_title),
                                             type='page',
                                             representation='storage',
                                             editor='v2'):
                print('+++ page {} is ready in space {}'.format(page_title, page_space))
    
    def update_confluence_page(self,
                               page_space,
                               page_title,
                               page_body,):
        if self.__confluence.page_exists(space=page_space, title=page_title):
            self.__confluence.update_page(page_id=self.__confluence.get_page_id(space=page_space, title=page_title),
                                          title=page_title,
                                          body=page_body)


class FileSystemStatistics:
    def __init__(self, username, token) -> None:
        self.username = username
        self.token = token
        self.CP = ConfluencePage(username=self.username, token=self.token)
        if not "statistics" in os.listdir():
            os.mkdir("statistics")

    def get_list_required_pages(self):
        """
            Получаем дочерние страницы 1.7: 1.7.1; 1.7.2; 1.7.n...
        """
        children_main_page = self.CP.get_child_page_as_html(id="156339086", by_title=False)
        """
            required_page - ID родительской страницы в каждой версии, в которой находится список отчетов
        """
        required_pages = []
        """
            Проходим по всем версиям
        """
        for id_children_from_main_page in children_main_page:
            """
                Получаем ID страниц PostgreSQL, Системные службы, Файловые системы в каждой конкретной версии
            """
            astra_version_child_pages = self.CP.get_child_page_as_html(id=id_children_from_main_page, by_title=False)
            """
                Проходим по каждому полученному ID
            """
            for page_id in astra_version_child_pages:
                """
                    Получаем информацию о странице
                """
                page = self.CP.get_page_as_html(id=page_id)
                """
                    Проверяем есть ли в заголовке Файловые системы и имеются ли дочерние страницы
                """
                if "Файловые системы" in page.get("title") and self.CP.get_child_page_as_html(id=page_id):
                    required_pages.append(page_id)

        return required_pages
    
    def get_info_from_pages(self, pages):
        def build_main_dataframe(fs_type, data_fs, stand):
            columns = ['Релиз', 'Ядро', 'Режим защищенности', 'Стенд', 'Рейтинг', 'Рейтинг2']
            df = pd.DataFrame(data=data_fs, columns=columns)

            df = df.sort_values(by=['Режим защищенности', 'Релиз'], ascending=[True, True])
            
            panda_series = df['Рейтинг2']
            df.insert(0, "№", [x for x in range(1, len(panda_series.tolist()) + 1, 1)])
            df = df.drop('Рейтинг2', axis=1)
            
            table = df.to_html(escape=False, index=False)
            f_ext4 = open(f"statistics/fs_{fs_type}_{stand}_1.html", 'w')
            f_ext4.writelines(f"<h1>Сводная таблица результатов тестирования {fs_type} {stand}</h1> {table}")
            f_ext4.close()
            
            return panda_series.tolist(), df['Релиз'] + '_' + df['Ядро']
        
        def build_mat_stat_dataframe(fs_type, data_fs, stand):
            min_znach = min(data_fs)
            max_znach = max(data_fs)
            mean = round(np.mean(data_fs), 3)
            median = round(np.median(data_fs), 3)
            std = round(np.std(data_fs), 3)
            var = round(np.var(data_fs), 3)

            """
                TODO Дописать отклоение от MIN MAX
            """

            data_for_mat_stat = np.array(
                [
                    ['MIN', min_znach],
                    ['MAX', max_znach],
                    ['Мат. ожидание', mean],
                    ['Медиана', median],
                    ['Стандартное отклонение', std],
                    ['Дисперсия', var],
                ]
            )
        
            df_mat_stat = pd.DataFrame(data=data_for_mat_stat, columns=['Оценка', 'Значение'])
            """
                Строим вторую HTML таблицу
            """
            mat_stat_table_html = df_mat_stat.to_html(index=False)
            new_file_html = open(f"statistics/fs_{fs_type}_{stand}_2.html", 'w')
            new_file_html.write(f'<h1>Таблица основных статистических параметров {fs_type} {stand}</h1> {mat_stat_table_html}')
            new_file_html.close()

        def build_graph(fs_type, stand, rating_fg, shcala_txt):
            colors = []
            
            for temp in rating_fg:
                #if temp < np.mean(data_ratings.get('rating')) - 2 * np.std(data_ratings.get('rating')) or temp > np.mean(data_ratings.get('rating')) + 2 * np.std(data_ratings.get('rating')):
                if temp < np.mean(rating_fg) - 1 * np.std(rating_fg):
                    colors.append("#ea5c76")
                elif temp > np.mean(rating_fg) + 1 * np.std(rating_fg):
                    colors.append("#ffc322")
                else:
                    colors.append("#c7d84c")
            shcala_x = [x for x in range(1, len(rating_fg) + 1, 1)]
            fig, ax = plt.subplots(figsize=(16, 9))

            ax.bar(shcala_x, rating_fg, color=colors)
            ax.set_xticks(shcala_x)
            ax.set_ylim([0, max(rating_fg) + max(rating_fg) * 0.15])
            # ax.set_xticklabels(shcala_txt)
            # plt.xticks(shcala_txt)
            # fig.autofmt_xdate(rotation=25)
            plt.gca().set_xticklabels(shcala_txt, rotation=20, horizontalalignment='right')
            ax.grid(False)
            # ax.set_xlabel("Порядковый номер теста")
            ax.set_ylabel("Значение рейтинга")
            ax.set_title(f"{fs_type}. Сравнительная диаграмма значений рейтингов, \nвычисленных на основании результатов нагрузочного тестирования. \n {stand}")
            for i, val in enumerate(rating_fg):
                try:
                    val = int(val)
                except ValueError:
                    pass
                plt.text(i + 1, val * 0.5, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
            red_patch = mpatches.Patch(color='#ea5c76', label='Рейтинг ниже мат. ожидания на величину превышающую стандартное отклонение')
            green_patch = mpatches.Patch(color='#c7d84c', label='Рейтинг соответвует доверительному интервалу')
            yellow_patch = mpatches.Patch(color='#ffc322', label='Рейтинг выше мат. ожидания на величину превышающую стандартное отклонение')
            ax.legend(handles=[red_patch, green_patch, yellow_patch])
            fig.savefig(f"statistics/fs_{fs_type}_{stand}.png")

        file_system_data = {
            'data': {
                'stand1': {
                    'EXT4': [],
                    'EXT4_parsec': [],
                    'NTFS': [],
                    'NTFS_parsec': [],
                    'XFS': [],
                    'XFS_parsec': []
                },
                'stand2': {
                    'EXT4': [],
                    'EXT4_parsec': [],
                    'NTFS': [],
                    'NTFS_parsec': [],
                    'XFS': [],
                    'XFS_parsec': []
                },
                'stand3': {
                    'EXT4': [],
                    'EXT4_parsec': [],
                    'NTFS': [],
                    'NTFS_parsec': [],
                    'XFS': [],
                    'XFS_parsec': []
                },
                'stand4': {
                    'EXT4': [],
                    'EXT4_parsec': [],
                    'NTFS': [],
                    'NTFS_parsec': [],
                    'XFS': [],
                    'XFS_parsec': []
                },
            }
        }

        for id in pages:
            """
                Получаем список отчетов
            """
            list_pages_with_report = self.CP.get_child_page_as_html(id)
            for title in list_pages_with_report:
                """
                    Получаем конкретную страницу отчета
                """
                src_html = self.CP.get_page_as_html(page_space="DD", page_title=title)
                data = src_html.get("body").get("view").get("value")
                soup = BeautifulSoup(data, 'lxml')
                temp_data = title.replace(" ", "_").split("_")
            
                if temp_data[1] == "parsec":
                    type_fs, parsec, astra_version, sec_mode, kernel, stand = temp_data[0], temp_data[1], temp_data[2], temp_data[3], temp_data[4], temp_data[5]
                else:
                    parsec = ""
                    type_fs, astra_version, sec_mode, kernel, stand = temp_data[0], temp_data[1], temp_data[2], temp_data[3], temp_data[4]
                """
                    Выдергиваем значение рейтинга из html страницы
                """
                rating = soup.find(string=re.compile("[Tt]otal rating")).strip().split(" ")[2]
                """
                    Генерируем ссылку на отчет
                """
                link = "https://life.astralinux.ru/display/DD/" + title 
                rating_with_link = f'<a href="{link}">{rating}</a>'

                if parsec:
                    temp_key_fs_with_parsec = f'{type_fs}_{parsec}'
                else:
                    temp_key_fs_with_parsec = type_fs

                if type_fs == "OCFS2":
                    continue
                file_system_data['data'][stand][temp_key_fs_with_parsec].append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])

        for key, data in file_system_data['data'].items():
            # print(key, data)
            if data.get("EXT4"):
                rating_for_graph, shcl = build_main_dataframe("EXT4", data.get("EXT4"), key)
                build_mat_stat_dataframe("EXT4", rating_for_graph, key)
                build_graph(fs_type="EXT4", stand=key, rating_fg=rating_for_graph, shcala_txt=shcl)
            if data.get("EXT4_parsec"):
                rating_for_graph, shcl = build_main_dataframe("EXT4_parsec", data.get("EXT4_parsec"), key)
                build_mat_stat_dataframe("EXT4_parsec", rating_for_graph, key)
                build_graph(fs_type="EXT4_parsec", stand=key, rating_fg=rating_for_graph, shcala_txt=shcl)
            if data.get("NTFS"):
                rating_for_graph, shcl = build_main_dataframe("NTFS", data.get("NTFS"), key)
                build_mat_stat_dataframe("NTFS", rating_for_graph, key)
                build_graph(fs_type="NTFS", stand=key, rating_fg=rating_for_graph, shcala_txt=shcl)
            if data.get("NTFS_parsec"):
                rating_for_graph, shcl = build_main_dataframe("NTFS_parsec", data.get("NTFS_parsec"), key)
                build_mat_stat_dataframe("NTFS_parsec", rating_for_graph, key)
                build_graph(fs_type="NTFS_parsec", stand=key, rating_fg=rating_for_graph, shcala_txt=shcl)
            if data.get("XFS"):
                rating_for_graph, shcl = build_main_dataframe("XFS", data.get("XFS"), key)
                build_mat_stat_dataframe("XFS", rating_for_graph, key)
                build_graph(fs_type="XFS", stand=key, rating_fg=rating_for_graph, shcala_txt=shcl)
            if data.get("XFS_parsec"):
                rating_for_graph, shcl = build_main_dataframe("XFS_parsec", data.get("XFS_parsec"), key)
                build_mat_stat_dataframe("XFS_parsec", rating_for_graph, key)
                build_graph(fs_type="XFS_parsec", stand=key, rating_fg=rating_for_graph, shcala_txt=shcl)

    """
        Создаем итоговую html страницу для life
    """
    def upload_statistics(self, type_stat='PostgreSQL'):
        confluence_stat = StatisticsToConfluence(username=self.username, token=self.token)
        confluence_stat.create_confluence_page(page_space="DD", page_title=f"Статистика. {type_stat}", parent_page_title="Статистика")

         ### TODO изменить пространство и parent_page_title

        template_img = """ 
            <p>
                <br/>
            </p>
            <hr/>
            <br/>
            <span class="confluence-embedded-file-wrapper confluence-embedded-manual-size">
                <img class="confluence-embedded-image" draggable="false" src="/download/attachments/{page_id}/{img_png}" data-image-src="/download/attachments/{page_id}/{img_png}" data-unresolved-comment-count="0" data-linked-resource-id="{page_id}" data-linked-resource-version="1" data-linked-resource-type="attachment" data-linked-resource-default-alias="{img_png}" data-base-url="https://life.astralinux.ru" data-linked-resource-content-type="image/png" data-linked-resource-container-id="{page_id}" data-linked-resource-container-version="6" ></img>
            </span>
            <br/>
            <h1><a href="https://life.astralinux.ru/pages/viewpage.action?pageId=192234259">Описание стендов нагрузочного тестирования</a></h1>
        """
        image_list = []
        table_with_data_list = []
        table_with_mat_stat_list = []
        
        for file in sorted(os.listdir("statistics")):
            # print(file)
        
            if file.endswith("png"):
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                image_list.append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file))
            if file.endswith("1.html"):
                file_table = open(f'statistics/{file}', 'r')
                table = file_table.read()
                file_table.close()
                table_with_data_list.append(table)
            if file.endswith("2.html"):
                new_file_table = open(f'statistics/{file}', 'r')
                mat_stat_table = new_file_table.read()
                new_file_table.close()
                table_with_mat_stat_list.append(mat_stat_table)
        

        html_list = []

        for ind, item in enumerate(table_with_data_list):
            html_list.append(image_list[ind])
            # html_list.append("<hr>")
            html_list.append(item)
            html_list.append("<br/>" + table_with_mat_stat_list[ind])
        
        html_page = "".join(html_list)

        confluence_stat.update_confluence_page(page_space="DD", page_title=f"Статистика. {type_stat}", page_body=html_page)


    def update_statistics(self):
        pages = self.get_list_required_pages()
        self.get_info_from_pages(pages=pages)
        self.upload_statistics(type_stat="Файловые системы")