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
                if stand == "stand2":
                    data_for_df["stand2"]["data"].append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])
                if stand == "stand3":
                    data_for_df["stand3"]["data"].append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])
                if stand == "stand4":
                    data_for_df["stand4"]["data"].append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])

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
            
            df_5_10 = df[df["Ядро"].str.contains('5.10', case=False)]
            df_5_10['Ядро'] = '5.10'
            temp_data_kernel['5.10'].append(df_5_10[['Релиз', 'Ядро', 'Стенд', 'rating_2']])
            df_5_15_gen = df[df["Ядро"].str.contains('5.15\S*generic', case=False, regex=True)]
            df_5_15_gen['Ядро'] = '5.15-gen'
            temp_data_kernel["5.15-gen"].append(df_5_15_gen[['Релиз', 'Ядро', 'Стенд', 'rating_2']])
            df_5_15_ll = df[df["Ядро"].str.contains('5.15\S*low', case=False, regex=True)]
            df_5_15_ll['Ядро'] = '5.15-ll'
            temp_data_kernel["5.15-ll"].append(df_5_15_ll[['Релиз', 'Ядро', 'Стенд', 'rating_2']])

            df.insert(0, "№", [x for x in range(1, len(panda_series.tolist()) + 1, 1)])

            df = df.drop('rating_2', axis=1)
            temp_data_for_graph[key] = (df['Релиз'] + "_" + df['Ядро'])

            """
                Строим HTML
            """
            statistics_table_html = df.to_html(escape=False, index=False)
            file_html = open(f"statistics/{name_html}_{key}_1.html", "w")
            file_html.writelines('<h1><a href="https://life.astralinux.ru/pages/viewpage.action?pageId=192234259">Описание стендов нагрузочного тестирования</a></h1>')
            file_html.writelines(f"<h1>Сводная таблица результатов тестирования {key}</h1> {statistics_table_html}")
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
                ax.set_title(f"PostgreSQL. Сводная диаграмма сравнения по ядрам.\n{list(data_kernel['Стенд'])[0]} - {kernel}")
                for i, val in enumerate(data_kernel['rating_2']):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                    plt.text(i, val * 0.5, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
                fig.savefig(f"statistics/postresql_statistics_{list(data_kernel['Стенд'])[0]}_{kernel}.jpg")
        
        df_merged = pd.DataFrame()
        array_merged_dataframes = []
        for key_kernel, tdf2 in temp_data_kernel.items():
            first_passed = False
            for i, item in enumerate(tdf2):
                try:
                    if not first_passed:
                        df_merged = pd.merge(item, tdf2[i + 1], how='outer', left_on=["Релиз", "Ядро"], right_on=["Релиз", "Ядро"])
                        first_passed = True
                    else:
                        df_merged = pd.merge(df_merged, tdf2[i + 1], how='outer', left_on=["Релиз", "Ядро"], right_on=["Релиз", "Ядро"])
                except IndexError:
                    break
            array_merged_dataframes.append(df_merged)
        
        for merged_df in array_merged_dataframes:
            ratings_for_plt_graph = merged_df.iloc[::, 3::2]
            names_stand = merged_df.iloc[::, 2::2].mode().iloc[0].tolist()
            title = merged_df['Ядро'].mode()[0]
            fig, ax = plt.subplots(figsize=(12.8, 7.2))
            ax.grid(True, alpha=.6)
            ax.set_title(f"PostgreSQL. Сводная диаграмма сравнения по стендам.\n{title}")
            colors = ['#f90829', '#007b7a', '#f9b312', '#c7d84c']
            for index in range(ratings_for_plt_graph.shape[1]):
                ax.plot(merged_df['Релиз'], ratings_for_plt_graph.iloc[::, index], "o-", color=colors[index])
            plt.legend(names_stand)

            # Lighten borders
            plt.gca().spines["top"].set_alpha(.0)
            plt.gca().spines["bottom"].set_alpha(.3)
            plt.gca().spines["right"].set_alpha(.0)
            plt.gca().spines["left"].set_alpha(.3)

            plt.savefig(f"statistics/postresql_statistics_all_stands_{title}_kernel.jpg")


    """
        Создаем итоговую html страницу для life
    """
    def upload_statistics(self, type_stat='PostgreSQL'):
        confluence_stat = StatisticsToConfluence(username=self.username, token=self.token)
        confluence_stat.create_confluence_page(page_space="DD", page_title=f"Статистика. {type_stat}", parent_page_title="Статистика")

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

        lst_all_stands_stat_kernel = []


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
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                kernel_image_list[0].append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file))
            if file.endswith("5.15-gen.jpg"):
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                kernel_image_list[1].append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file))
            if file.endswith("5.15-ll.jpg"):
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                kernel_image_list[2].append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file))
            if file.endswith("kernel.jpg"):
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                lst_all_stands_stat_kernel.append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                  img_png=file))

        html_list = []


        for item in lst_all_stands_stat_kernel:
            html_list.append(item)

        for ind, item in enumerate(table_with_data_list):
            html_list.append(image_list[ind])
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




class PSQLStatistics2:
    
    def __init__(self, username, token) -> None:
        self.username = username
        self.token = token
        self.CP = ConfluencePage(username=self.username, token=self.token)
        if not "statistics" in os.listdir():
            os.mkdir("statistics")


    @staticmethod
    def get_grade(stand):
            if stand == "stand1":
                grade = "low"
            elif stand == "stand2":
                grade = "low"
            elif stand == "stand3":
                grade = "middle"
            elif stand == "stand4":
                grade = "high"
            else:
                grade = stand
            return grade


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

    
    def get_info_from_pages(self, pages, columns_df):
        def collect_data(test_name="postgresql"):
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
                    
                    if temp_data[0] == test_name:
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
                        if stand == "stand2":
                            data_for_df["stand2"]["data"].append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])
                        if stand == "stand3":
                            data_for_df["stand3"]["data"].append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])
                        if stand == "stand4":
                            data_for_df["stand4"]["data"].append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])
            return data_for_df

        def build_dataframes(data_for_df, test_name):
            """
                Строим таблицу №1
            """
            dataframes_for_summary_graph = {}
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
                
                df_5_10 = df[df["Ядро"].str.startswith('5.10')]
                # print(df_5_10)
                df_5_10['Ядро'] = '5.10'
                temp_data_kernel['5.10'].append(df_5_10[['Релиз', 'Ядро', 'Стенд', 'rating_2']])
                df_5_15_gen = df[df["Ядро"].str.contains('5.15\S*generic', case=False, regex=True)]
                df_5_15_gen['Ядро'] = '5.15-gen'
                temp_data_kernel["5.15-gen"].append(df_5_15_gen[['Релиз', 'Ядро', 'Стенд', 'rating_2']])
                df_5_15_ll = df[df["Ядро"].str.contains('5.15\S*low', case=False, regex=True)]
                df_5_15_ll['Ядро'] = '5.15-ll'
                temp_data_kernel["5.15-ll"].append(df_5_15_ll[['Релиз', 'Ядро', 'Стенд', 'rating_2']])

                df.insert(0, "№", [x for x in range(1, len(panda_series.tolist()) + 1, 1)])

                dataframes_for_summary_graph[key] = df[['Релиз', 'Ядро', 'Стенд', 'rating_2']]

                df = df.drop('rating_2', axis=1)
                temp_data_for_graph[key] = (df['Релиз'] + "_" + df['Ядро'])
                """
                Строим HTML
                """
                statistics_table_html = df.to_html(escape=False, index=False)
                file_html = open(f"statistics/{test_name}_{key}_1.html", "w")
                file_html.writelines('<h1><a href="https://life.astralinux.ru/pages/viewpage.action?pageId=192234259">Описание стендов нагрузочного тестирования</a></h1>')
                grage = self.get_grade(key)
                file_html.writelines(f"<h1>Сводная таблица результатов тестирования {grage}_{key}</h1> {statistics_table_html}")
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
                new_file_html = open(f"statistics/{test_name}_{key}_2.html", 'w')
                new_file_html.write(mat_stat_table_html)
                new_file_html.close()

            return temp_data_for_graph, temp_data_kernel, dataframes_for_summary_graph
        
        def create_graphs(data_for_df, test_name, temp_data_for_graph):
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
                grade = self.get_grade(key)
                ax.set_title(f"PostgreSQL. Сравнительная диаграмма значений рейтингов, \nвычисленных на основании результатов нагрузочного тестирования. \n {grade}_{key}")
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
                fig.savefig(f"statistics/{test_name}_statistics_{key}.png")


        def create_comparison_kernel_graph(temp_data_kernel, test_name):
            for kernel, temp_data_frame in temp_data_kernel.items():
                for data_kernel in temp_data_frame:
                    fig, ax = plt.subplots(figsize=(12.8, 7.2))
                    ax.bar(data_kernel['Релиз'], data_kernel['rating_2'], color="#a3d1cd")
                    grade = self.get_grade(list(data_kernel['Стенд'])[0])
                    ax.set_title(f"PostgreSQL. Сводная диаграмма сравнения по ядрам.\n{grade}_{list(data_kernel['Стенд'])[0]} - {kernel}")
                    for i, val in enumerate(data_kernel['rating_2']):
                        try:
                            val = int(val)
                        except ValueError:
                            pass
                        plt.text(i, val * 0.5, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
                    fig.savefig(f"statistics/{test_name}_statistics_{list(data_kernel['Стенд'])[0]}_{kernel}.jpg")
        

        def create_comparison_kernel_and_stand_graph(temp_data_kernel, test_name):
            df_merged = pd.DataFrame()
            array_merged_dataframes = []
            for key_kernel, tdf2 in temp_data_kernel.items():
                first_passed = False
                for i, item in enumerate(tdf2):
                    try:
                        if not first_passed:
                            df_merged = pd.merge(item, tdf2[i + 1], how='outer', left_on=["Релиз", "Ядро"], right_on=["Релиз", "Ядро"])
                            first_passed = True
                        else:
                            df_merged = pd.merge(df_merged, tdf2[i + 1], how='outer', left_on=["Релиз", "Ядро"], right_on=["Релиз", "Ядро"])
                    except IndexError:
                        break
                array_merged_dataframes.append(df_merged)
            
            for merged_df in array_merged_dataframes:
                ratings_for_plt_graph = merged_df.iloc[::, 3::2]
                names_stand = merged_df.iloc[::, 2::2].mode().iloc[0].tolist()
                grades = list(map(self.get_grade, names_stand))
                grades_with_stands = list(map(lambda x, y: x + "_" + y, grades, names_stand))
                title = merged_df['Ядро'].mode()[0]
                fig, ax = plt.subplots(figsize=(12.8, 7.2))
                ax.grid(True, alpha=.6)
                ax.set_title(f"PostgreSQL. Сводная диаграмма сравнения по стендам.\n{title}")
                colors = ['#f90829', '#007b7a', '#f9b312', '#c7d84c']
                for index in range(ratings_for_plt_graph.shape[1]):
                    ax.plot(merged_df['Релиз'], ratings_for_plt_graph.iloc[::, index], "o-", color=colors[index])
                plt.legend(grades_with_stands)

                # Lighten borders
                plt.gca().spines["top"].set_alpha(.0)
                plt.gca().spines["bottom"].set_alpha(.3)
                plt.gca().spines["right"].set_alpha(.0)
                plt.gca().spines["left"].set_alpha(.3)

                plt.savefig(f"statistics/{test_name}_statistics_all_stands_{title}_kernel.jpg")
        
        def create_summary_table(dfs1, dfs2):
            merged_dataframes = []
            for stand, df in dfs2.items():
                if stand in dfs1.keys():
                    df_temp = pd.merge(dfs1[stand], dfs2[stand], how='outer', left_on=["Релиз", "Ядро", "Стенд"], right_on=["Релиз", "Ядро", "Стенд"])
                    merged_dataframes.append(df_temp)
            return merged_dataframes
        
        def create_summary_graph(merged_df):
            for df in merged_df:
                shcala_x = [x for x in range(len(df['Релиз']))]
                fig, ax = plt.subplots(figsize=(16, 9))
                grade = self.get_grade(df['Стенд'].mode()[0])
                ax.set_title(f"Сравнительная диаграмма значений рейтингов PSQL orel/smolensk.\n{grade}_{df['Стенд'].mode()[0]}")
                # ax.grid(True, alpha=.3)
                ax.set_ylabel("Значение рейтинга")
                ax.set_ylim([0, max(df['rating_2_x'].fillna(0)) + max(df['rating_2_x'].fillna(0)) * 0.2])
                df['version'] = df['Релиз'] + "_" + df['Ядро']
                ax.bar(df['version'], df['rating_2_x'], color='#88c1f2')
                ax.bar(df['version'], df['rating_2_y'], color='#ea5c76', alpha=0.9, width=0.7)
                plt.xticks(rotation=20, horizontalalignment='right')
                for i, val in enumerate(df['rating_2_x']):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                    if val != 0:
                        plt.text(i, val, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
                for i, val in enumerate(df['rating_2_y']):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                    if val != 0:
                        plt.text(i, val * 0.5, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
                ax.legend(["Orel", "Smolensk"])
                plt.savefig(f"statistics/summ_graph_{df['Стенд'].mode()[0]}_summ.jpg")
                
            
        
        data_df_orel = collect_data(test_name="postgresql")
        tmp_data_for_gr, tmp_data_krnl, df_psql = build_dataframes(data_for_df=data_df_orel, test_name="postgresql")
        create_graphs(data_for_df=data_df_orel, test_name="postgresql", temp_data_for_graph=tmp_data_for_gr)
        create_comparison_kernel_graph(temp_data_kernel=tmp_data_krnl, test_name="postgresql")
        create_comparison_kernel_and_stand_graph(temp_data_kernel=tmp_data_krnl, test_name="postgresql")
        
        data_df_smolensk = collect_data(test_name="postgresql-sm")
        tmp_data_for_gr_smol, tmp_data_krnl_smol, df_psql_sm = build_dataframes(data_for_df=data_df_smolensk, test_name="postgresql-sm")
        create_graphs(data_for_df=data_df_smolensk, test_name="postgresql-sm", temp_data_for_graph=tmp_data_for_gr_smol)

        data_df_orel_audit_off = collect_data(test_name="postgresql-aud-off")
        tmp_data_for_gr_audit_off, tmp_data_krnl_audit_off, df_psql_audit_off = build_dataframes(data_for_df=data_df_orel_audit_off, test_name="postgresql-aud-off")
        create_graphs(data_for_df=data_df_orel_audit_off, test_name='postgresql-aud-off', temp_data_for_graph=tmp_data_for_gr_audit_off)

        summ_df = create_summary_table(dfs1=df_psql, dfs2=df_psql_sm)
        create_summary_graph(merged_df=summ_df)


    """
        Создаем итоговую html страницу для life
    """
    def upload_statistics(self, type_stat='PostgreSQL'):
        
        confluence_stat = StatisticsToConfluence(username=self.username, token=self.token)
        confluence_stat.create_confluence_page(page_space="DD", page_title=f"Статистика. {type_stat}", parent_page_title="Статистика")

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

        image_list, images_list_smolensk, image_list_aud_off = [], [], []
        table_with_data_list, table_with_data_list_smolensk, table_with_data_list_aud_off = [], [], []
        table_with_mat_stat_list, table_with_mat_stat_list_smolensk, table_with_mat_stat_list_aud_off = [], [], []
        kernel_image_list = [[], [], []]
        lst_all_stands_stat_kernel = []

        summ_graphs_list = []

        header_orel, header_smolensk, header_orel_vs_smolensk, header_aud_off = [], [], [], []

        for file in sorted(os.listdir("statistics")):
            if file.endswith("png"):
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                img = template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file)
                name_stand = file.split("_")[2].split(".")[0]
                if file.startswith("postgresql-sm"):
                    images_list_smolensk.append(img)
                    header_smolensk.append(name_stand)
                elif file.startswith('postgresql-aud-off'):
                    image_list_aud_off.append(img)
                    header_aud_off.append(name_stand)
                elif file.startswith("postgresql-vo"):
                    pass
                else:
                    image_list.append(img)
                    header_orel.append(name_stand)

            if file.endswith("1.html"):
                file_table = open(f'statistics/{file}', 'r')
                table = file_table.read()
                file_table.close()
                if file.startswith("postgresql-sm"):
                    table_with_data_list_smolensk.append(table)
                elif file.startswith('postgresql-aud-off'):
                    table_with_data_list_aud_off.append(table)
                elif file.startswith("postgresql-vo"):
                    pass
                else:
                    table_with_data_list.append(table)

            if file.endswith("2.html"):
                new_file_table = open(f'statistics/{file}', 'r')
                mat_stat_table = new_file_table.read()
                new_file_table.close()
                if file.startswith("postgresql-sm"):
                    table_with_mat_stat_list_smolensk.append(mat_stat_table)
                elif file.startswith('postgresql-aud-off'):
                    table_with_mat_stat_list_aud_off.append(mat_stat_table)
                elif file.startswith("postgresql-vo"):
                    pass
                else:
                    table_with_mat_stat_list.append(mat_stat_table)

            if file.endswith("5.10.jpg"):
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                kernel_image_list[0].append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file))
            if file.endswith("5.15-gen.jpg"):
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                kernel_image_list[1].append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file))
            if file.endswith("5.15-ll.jpg"):
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                kernel_image_list[2].append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file))
            if file.endswith("kernel.jpg"):
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                lst_all_stands_stat_kernel.append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                  img_png=file))
            
            if file.endswith("summ.jpg"):
                confluence_stat.attache_files(file=f'statistics/{file}', page_space="DD", page_title=f"Статистика. {type_stat}")
                summ_graphs_list.append(template_img.format(page_id=confluence_stat.get_confluence_page_id("DD", f"Статистика. {type_stat}"),
                                                    img_png=file))
                name_stand = file.split("_")[2]
                header_orel_vs_smolensk.append(name_stand)
                
        html_list = []

        nav_start = """
            <nav>
            <h2>Содержание:</h2>
            <ul>
        """
        nav_end = """
            </ul>
            </nav>
        """
        nav_lst_orel, nav_lst_smolensk, nav_lst_orel_vs_smolensk, nav_lst_aud_off = [], [], [], []
        nav_body = '''
            <li><a href="#id-Статистика.PostgreSQL-Orel">Orel</a>
                <ul>
                    {list_orel}
                </ul>
            </li>
            <li><a href="#id-Статистика.PostgreSQL-Smolensk">Smolensk</a>
                <ul>
                    {list_smolensk}
                </ul>
            </li>
            <li><a href="#id-Статистика.PostgreSQL-OrelvsSmolensk">Orel vs Smolensk</a>
                <ul>
                    {list_orel_vs_smolensk}
                </ul>
            </li>
            <li><a href="#id-Статистика.PostgreSQL-Orelauditoff">Orel audit off</a>
                <ul>
                    {list_aud_off}
                </ul>
            </li>
        '''

        html_list.append('<hr/><h1 style="text-align: center;">Orel</h1>')
        for item in lst_all_stands_stat_kernel:
            html_list.append(item)
        
        for ind, item in enumerate(table_with_data_list):
            grade = self.get_grade(header_orel[ind])
            nav_lst_orel.append(f'<li><a href="#id-Статистика.PostgreSQL-{grade}_{header_orel[ind]}">{grade}_{header_orel[ind]}</a></li>')
            html_list.append(f"<hr/><h1>{grade}_{header_orel[ind]}</h1>")
            html_list.append(image_list[ind])
            html_list.append(item)
            html_list.append("<h1>Таблица основных статистических параметров.</h1>" + "<br/>" + table_with_mat_stat_list[ind])
            
            html_list.append(kernel_image_list[0][ind])
            html_list.append(kernel_image_list[1][ind])
            html_list.append(kernel_image_list[2][ind])

        html_list.append('<h1 style="text-align: center;">Smolensk</h1>')
        for ind, item in enumerate(table_with_data_list_smolensk):
            grade = self.get_grade(header_smolensk[ind])
            nav_lst_smolensk.append(f'<li><a href="#id-Статистика.PostgreSQL-{grade}_{header_smolensk[ind]}.1">{grade}_{header_smolensk[ind]}</a></li>')
            html_list.append(f"<hr/><h1>{grade}_{header_smolensk[ind]}</h1>")
            html_list.append(images_list_smolensk[ind])
            html_list.append(item)
            html_list.append("<h1>Таблица основных статистических параметров.</h1>" + "<br/>" + table_with_mat_stat_list_smolensk[ind])
        
        html_list.append('<h1 style="text-align: center;">Orel vs Smolensk</h1>')
        for ind, item in enumerate(summ_graphs_list):
            grade = self.get_grade(header_orel_vs_smolensk[ind])
            nav_lst_orel_vs_smolensk.append(f'<li><a href="#id-Статистика.PostgreSQL-{grade}_{header_orel_vs_smolensk[ind]}.2">{grade}_{header_orel_vs_smolensk[ind]}</a></li>')
            html_list.append(f"<hr/><h1>{grade}_{header_orel_vs_smolensk[ind]}</h1>")
            html_list.append(item)

        html_list.append('<h1 style="text-align: center;">Orel audit off</h1>')
        for ind, item in enumerate(table_with_data_list_aud_off):
            grade = self.get_grade(header_aud_off[ind])
            nav_lst_aud_off.append(f'<li><a href="#id-Статистика.PostgreSQL-{grade}_{header_aud_off[ind]}.3">{grade}_{header_aud_off[ind]}</a></li>')
            html_list.append(f"<hr/><h1>{grade}_{header_aud_off[ind]}</h1>")
            html_list.append(image_list_aud_off[ind])
            html_list.append(item)
            html_list.append("<h1>Таблица основных статистических параметров.</h1>" + "<br/>" + table_with_mat_stat_list_aud_off[ind])



        nav = nav_start + nav_body.format(list_orel="".join(nav_lst_orel), list_smolensk="".join(nav_lst_smolensk), list_orel_vs_smolensk="".join(nav_lst_orel_vs_smolensk), list_aud_off="".join(nav_lst_aud_off)) + nav_end

        html_list.insert(0, nav)

        html_page = "".join(html_list)

        confluence_stat.update_confluence_page(page_space="DD", page_title=f"Статистика. {type_stat}", page_body=html_page)

    def update_statistics(self):
        pages = self.get_list_required_pages()
        columns = ["Релиз", "Ядро", "Режим защищенности", "Стенд", "PostgreSQL_11 rating", 'rating_2']

        self.get_info_from_pages(pages=pages, columns_df=columns)
        self.upload_statistics()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-u', '--username',
                        action='store',
                        required=True,
                        help='confluence user',
                        dest='USER')
    parser.add_argument('-t', '--token',
                        action='store',
                        required=True,
                        default=None,
                        help='confluence access token',
                        dest='TOKEN')
    args = parser.parse_args()
    
    stat = PSQLStatistics2(username=args.USER, token=args.TOKEN)
    stat.update_statistics()