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


class PSQLStatistics:

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
                    Проверяем есть ли в заголовке PostreSQL и имеются ли дочерние страницы
                """
                if "PostgreSQL" in page.get("title") and self.CP.get_child_page_as_html(id=page_id):
                    required_pages.append(page_id)
                if "Системные службы" in page.get("title") and self.CP.get_child_page_as_html(id=page_id):
                    pass
                if "Файловые системы" in page.get("title") and self.CP.get_child_page_as_html(id=page_id):
                    pass

        return required_pages

    col = ["Релиз", "Ядро", "Режим защищенности", "Стенд", "PostgreSQL_11 rating", 'rating_2']

    def get_info_from_pages(self, pages, name_html, columns_df=col):

        data_for_df = {

                'stand1': {
                    "data": [],
                    "rating": []
                }, 
                'stand2': {
                    "data": [],
                    "rating": []
                },
                'stand3': {
                    "data": [],
                    "rating": []
                },
                'stand4': {
                    "data": [],
                    "rating": []
                }

        }  

        # ratings_low, ratings_middle, ratings_high = [], [], []

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
                temp_data = title.split("_")
                """
                    Выдергиваем версию Астры, режим защищенности, ядро, грейд из заголовка
                """
                astra_version, sec_mode, kernel, stand = temp_data[1], temp_data[2], temp_data[3], temp_data[4]
                """
                    Выдергиваем значение рейтинга из html страницы
                """
                rating = soup.find(string=re.compile("[Tt]otal rating")).strip().split(" ")[2]
                """
                    Генерируем ссылку на отчет
                """
                link = "https://life.astralinux.ru/display/DD/" + title 
                rating_with_link = f'<a href="{link}">{rating}</a>'
                """
                    Записываем полученные данные для дальнейшего составления DataFrame
                """
                if stand == "stand1":
                    data_for_df["stand1"]["data"].append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])
                    # data_for_df["stand1"]["rating"].append(float(rating))
                if stand == "stand2":
                    data_for_df["stand2"]["data"].append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])
                    # data_for_df["stand2"]["rating"].append(float(rating))
                if stand == "stand3":
                    data_for_df["stand3"]["data"].append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])
                    # data_for_df["stand3"]["rating"].append(float(rating))
                if stand == "stand4":
                    data_for_df["stand4"]["data"].append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])
                    # data_for_df["stand4"]["rating"].append(float(rating))

        """
            Строим таблицу №1
        """
        temp_data_for_graph = {}
        temp_data_kernel = {
            '5.10': [],
            '5.15-gen': [],
            '5.15-ll': []
        }
        for key, data in data_for_df.items():
            if len(data.get("data")) == 0:
                continue
            df = pd.DataFrame(data=data.get("data"), columns=columns_df, index=np.arange(1, len(data.get("data")) + 1))
            df = df.sort_values(by=['Режим защищенности', 'Релиз'], ascending=[True, True])
            
            panda_series = df['rating_2']
            data_for_df[key]['rating'] = panda_series.tolist()
            df.insert(0, "№", [x for x in range(1, len(panda_series.tolist()) + 1, 1)])
            
            df_5_10 = df[df["Ядро"].str.contains('5.10', case=False)]
            temp_data_kernel['5.10'].append(df_5_10)
            df_5_15_gen = df[df["Ядро"].str.contains('5.15\S*generic', case=False, regex=True)]
            temp_data_kernel["5.15-gen"].append(df_5_15_gen)
            df_5_15_ll = df[df["Ядро"].str.contains('5.15\S*low', case=False, regex=True)]
            temp_data_kernel["5.15-ll"].append(df_5_15_ll)

            # print(df_5_15_gen)

            df = df.drop('rating_2', axis=1)
            # print(type(df['Релиз'] + "_" + df['Ядро']))
            # print(df['Релиз'] + "_" + df['Ядро'])
            temp_data_for_graph[key] = (df['Релиз'] + "_" + df['Ядро'])

            """
                Строим HTML
            """
            statistics_table_html = df.to_html(escape=False, index=False)
            file_html = open(f"statistics/{name_html}_{key}_1.html", "w")
            file_html.writelines('<h1><a href="https://life.astralinux.ru/pages/viewpage.action?pageId=192234259">Описание стендов нагрузочного тестирования</a></h1>')
            file_html.writelines(f"<h1>Сводная таблица результатов тестирования {key}</h1> {statistics_table_html}")
            # file_html.write(statistics_table_html)
            file_html.close()

            data_rat = data.get('rating')

            min_znach = min(data_rat)
            max_znach = max(data_rat)
            mean = round(np.mean(data_rat), 3)
            median = round(np.median(data_rat), 3)
            std = round(np.std(data_rat), 3)
            var = round(np.var(data_rat), 3)
            
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
            new_file_html = open(f"statistics/{name_html}_{key}_2.html", 'w')
            new_file_html.write(mat_stat_table_html)
            new_file_html.close()


        """
            Cтроим графики
        """
        for key, data_ratings in data_for_df.items():
            if len(data_ratings.get("rating")) == 0:
                continue
            colors = []
            for temp in data_ratings.get('rating'):
                #if temp < np.mean(data_ratings.get('rating')) - 2 * np.std(data_ratings.get('rating')) or temp > np.mean(data_ratings.get('rating')) + 2 * np.std(data_ratings.get('rating')):
                if temp < np.mean(data_ratings.get('rating')) - 1 * np.std(data_ratings.get('rating')):
                    colors.append("#ea5c76")
                elif temp > np.mean(data_ratings.get('rating')) + 1 * np.std(data_ratings.get('rating')):
                    colors.append("#ffc322")
                else:
                    colors.append("#c7d84c")
            shcala_text = temp_data_for_graph[key]
            shcala = [x for x in range(1, len(data_ratings.get('rating')) + 1, 1)]
            fig, ax = plt.subplots(figsize=(16, 9))
            ax.bar(shcala, data_ratings.get('rating'), color=colors)
            ax.set_xticks(shcala)
            ax.set_ylim([0, max(data_ratings.get('rating')) + max(data_ratings.get('rating')) * 0.15])
            plt.gca().set_xticklabels(shcala_text, rotation=20, horizontalalignment= 'right')
            # ax.set_xlabel("Порядковый номер теста")
            ax.set_ylabel("Значение рейтинга")
            ax.set_title(f"PostgreSQL. Сравнительная диаграмма значений рейтингов, \nвычисленных на основании результатов нагрузочного тестирования. \n {key}")
            for i, val in enumerate(data_ratings.get("rating")):
                try:
                    val = int(val)
                except ValueError:
                    pass
                plt.text(i + 1, val * 0.5, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
            red_patch = mpatches.Patch(color='#ea5c76', label='Рейтинг ниже мат. ожидания на величину превышающую стандартное отклонение')
            green_patch = mpatches.Patch(color='#c7d84c', label='Рейтинг соответвует доверительному интервалу')
            yellow_patch = mpatches.Patch(color='#ffc322', label='Рейтинг выше мат. ожидания на величину превышающую стандартное отклонение')
            ax.legend(handles=[red_patch, green_patch, yellow_patch])
            fig.savefig(f"statistics/postresql_statistics_{key}.png")
            # plt.show()
            
        for kernel, temp_data_frame in temp_data_kernel.items():
            for data_kernel in temp_data_frame:
                fig, ax = plt.subplots(figsize=(12.8, 7.2))
                ax.bar(data_kernel['Релиз'], data_kernel['rating_2'], color="#a3d1cd")
                # print()
                ax.set_title(f"PostgreSQL. Сводная диаграмма сравнения по ядрам.\n{list(data_kernel['Стенд'])[0]} - {kernel}")
                for i, val in enumerate(data_kernel['rating_2']):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                    plt.text(i, val * 0.5, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
                fig.savefig(f"statistics/postresql_statistics_{list(data_kernel['Стенд'])[0]}_{kernel}.jpg")
        
        # print(sorted(os.listdir("statistics")))

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
                <img class="confluence-embedded-image" draggable="false" src="/download/attachments/{page_id}/{img_png}" data-image-src="/download/attachments/{page_id}/{img_png}" data-unresolved-comment-count="0" data-linked-resource-id="{page_id}" data-linked-resource-version="1" data-linked-resource-type="attachment" data-linked-resource-default-alias="{img_png}" data-base-url="https://life.astralinux.ru" data-linked-resource-content-type="image/png" data-linked-resource-container-id="{page_id}" data-linked-resource-container-version="6"></img>
            </span>
        """
        image_list = []
        table_with_data_list = []
        table_with_mat_stat_list = []

        kernel_image_list = [[], [], []]


        for file in sorted(os.listdir("statistics")):
            
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
            if file.endswith("5.10.jpg"):
                # print(file)
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                kernel_image_list[0].append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file))
            if file.endswith("5.15-gen.jpg"):
                # print(file)
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                kernel_image_list[1].append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file))
            if file.endswith("5.15-ll.jpg"):
                # print(file)
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                kernel_image_list[2].append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file))

        html_list = []

        # print(kernel_image_list)

        for ind, item in enumerate(table_with_data_list):
            # print(ind)
            html_list.append(image_list[ind])
            # html_list.append("<hr>")
            html_list.append(item)
            html_list.append("<h1>Таблица основных статистических параметров.</h1>" + "<br/>" + table_with_mat_stat_list[ind])
            
            html_list.append(kernel_image_list[0][ind])
            html_list.append(kernel_image_list[1][ind])
            html_list.append(kernel_image_list[2][ind])
        
        html_page = "".join(html_list)

        confluence_stat.update_confluence_page(page_space="DD", page_title=f"Статистика. {type_stat}", page_body=html_page)

    
    def update_statistics(self):
        pages = self.get_list_required_pages()
        self.get_info_from_pages(pages=pages, name_html="postresql")
        self.upload_statistics()
