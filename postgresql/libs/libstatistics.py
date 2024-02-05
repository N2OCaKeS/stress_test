import os
import re
import time
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from bs4 import BeautifulSoup
from atlassian import Confluence
from functools import reduce


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
                src_html = self.CP.get_page_as_html(page_space="DEVQA", page_title=title)
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
                link = "https://life.astralinux.ru/display/DEVQA/" + title 
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

SPACE = "DEVQA"

class PSQLStatistics2:
    
    def __init__(self, username, token) -> None:
        self.username = username
        self.token = token
        self.CP = ConfluencePage(username=self.username, token=self.token)
        if not "statistics" in os.listdir():
            os.mkdir("statistics")
        if not "statistics_rc" in os.listdir():
            os.mkdir("statistics_rc")
            time.sleep(1)


    @staticmethod
    def get_grade(stand):
            if stand == "stand1":
                grade = "Test-WorkStation"
            elif stand == "stand2":
                grade = "Test-WorkStation"
            elif stand == "stand3":
                grade = "LowServer"
            elif stand == "stand4":
                grade = "MiddleServer"
            else:
                grade = stand
            return grade


    def get_list_required_pages(self):
        """
            Получаем дочерние страницы 1.7: 1.7.1; 1.7.2; 1.7.n...
        """
        children_main_page = self.CP.get_child_page_as_html(id="156339086", by_title=False)
        # print(children_main_page)
        """
            required_page - ID родительской страницы в каждой версии, в которой находится список отчетов
        """
        required_pages, required_pages_rc = [], []
        test_dict_rc = {}

        """
            Проходим по всем версиям
        """
        for id_children_from_main_page in children_main_page:
            """
                Получаем ID страниц PostgreSQL, Системные службы, Файловые системы в каждой конкретной версии
            """
            try:
                name_page_original = self.CP.get_page_as_html(id=id_children_from_main_page).get("title")
                name_page = self.CP.get_page_as_html(id=id_children_from_main_page).get("title").split(" ⬝ ")[1]
                # print(name_page)
            except IndexError:
                pass
            
            astra_version_child_pages = self.CP.get_child_page_as_html(id=id_children_from_main_page, by_title=False)
            temp_arr = []
            # print(astra_version_child_pages)

            # test_dict_rc[name_page] = astra_version_child_pages
            """
                Проходим по каждому полученному ID
            """
            for page_id in astra_version_child_pages:
                # print("PAGE_ID", page_id)
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
                
                """
                    Статистика для RC
                """
                try:
                    name_child_page = page.get("title").split(" ⬝ ")[1]
                except IndexError:
                    pass
                if "1.7" in name_child_page or "1.8" in name_child_page:
                    hz_kak_nazvat_pages = self.CP.get_child_page_as_html(id=page_id, by_title=False)
                    if hz_kak_nazvat_pages:
                        for item_page in hz_kak_nazvat_pages:
                            if "PostgreSQL" in self.CP.get_page_as_html(id=item_page).get("title"):
                                temp_arr.append(item_page)

            test_dict_rc[name_page_original] = temp_arr

              
                # if not "1.7.5" in page.get("title"):
                #     continue
                
                # pre_title = page.get("title").split(" ⬝ ")
                # try:
                #     if not "UU" in pre_title[1]:
                #         rc_version = int(pre_title[1].split(".")[-1])
                #     else:
                #         rc_version = int(pre_title[1].split(".")[3])
                # except IndexError:
                #     pass
                # except ValueError:
                #     rc_version = None
                # if rc_version:
                #     rc_children = self.CP.get_child_page_as_html(id=page_id, by_title=False)
                #     print(pre_title, rc_version, rc_children)
                #     for item in rc_children:
                #         lst_page_rc = self.CP.get_child_page_as_html(id=item)
                #         print(lst_page_rc)                        
                        


                # #### ------ TESTING------###
                # try:
                #     title = page.get("title").split(" ⬝ ")
                #     print(title)
                #     if "1.7" in title[0]:
                #         pass ### Это страницы без DEBIAN и RHEL
                #     if not "UU" in title[1]:
                #         rc_version = int(title[1].split(".")[3])
                #     else:
                #         rc_version = int(title[1].split(".")[-1])
                #     if rc_version:
                #         child_rc_pages = self.CP.get_child_page_as_html(id=page_id, by_title=False)
                #         for child_rc_page in child_rc_pages:
                #             rc_page = self.CP.get_page_as_html(id=child_rc_page)
                #             if "PostgreSQL" in rc_page.get("title") and self.CP.get_child_page_as_html(id=child_rc_page):
                #                 required_pages_rc.append(child_rc_page)          
                # except IndexError:
                #     pass
                # except ValueError:
                #     continue
                
                ### ------ TESTING------###

        return required_pages, test_dict_rc

    
    def get_info_from_pages(self, pages, columns_df, rc=False, version_key=None):
        if rc and version_key:
            main_stat_dir = 'statistics_rc'
            stat_dir = f"{main_stat_dir}/{version_key}"
            if not version_key in os.listdir(main_stat_dir):
                os.mkdir(stat_dir)
        else:
            stat_dir = "statistics"

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
                    # print(title)
                    """
                        Получаем конкретную страницу отчета
                    """
                    src_html = self.CP.get_page_as_html(page_space=SPACE, page_title=title)
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
                        try:
                            if test_name == "psql balance":
                                num_failed_queries = soup.find(string='Number of failed queries').next_element.next_element.text
                                perc_failed_queries = soup.find(string='Percent of failed queries').next_element.next_element.text.split("%")[0]
                            rating = soup.find(string=re.compile("[Tt]otal rating")).strip().split(" ")[2]
                        except:
                            rating = 0
                        # print(rating)
                        """
                            Генерируем ссылку на отчет
                        """
                        link = f"https://life.astralinux.ru/display/{SPACE}/" + title 
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
                            if test_name == "psql balance":
                                num_failed_queries_with_link = f'<a href="{link}">{num_failed_queries}</a>'
                                perc_failed_queries_with_link = f'<a href="{link}">{perc_failed_queries}</a>'
                                data_for_df['stand4']['data'].append([astra_version, kernel, sec_mode, stand, num_failed_queries, perc_failed_queries, num_failed_queries_with_link, perc_failed_queries_with_link])
                            else:
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
                old_df = pd.DataFrame(data=data.get("data"), columns=columns_df, index=np.arange(1, len(data.get("data")) + 1))
                
                ### 
                """
                    Здесь должна быть реализации по удалению ненужных ядер из статистики
                    Например: Есть 2 протокола испытания версии 1.7.5, первый с ядром 6.1.29-1-generic и второй с ядром 6.1.50-1-generic.
                    Решение: Необходимо удалить строчки из DF(набора данных) с ядром 6.1.29-1-generic (Так как есть более новая версия ядра. Считать с 3 позиции: 29 < 50)
                """
                unique_versions = list(old_df['Релиз'].unique())
                new_df_temp = pd.DataFrame()
                for uniq_vers in unique_versions:
                    df_for_each_version = old_df.loc[old_df['Релиз'] == f'{uniq_vers}']
                    df_sort_5_10_gen = df_for_each_version[df_for_each_version["Ядро"].str.contains('5.10\S*generic', case=False, regex=True)]
                    df_sort_5_15_gen = df_for_each_version[df_for_each_version["Ядро"].str.contains('5.15\S*generic', case=False, regex=True)]
                    df_sort_5_15_ll = df_for_each_version[df_for_each_version["Ядро"].str.contains('5.15\S*low', case=False, regex=True)]
                    df_sort_6_1_gen = df_for_each_version[df_for_each_version["Ядро"].str.contains('6.1\S*generic', case=False, regex=True)]
                    dfs = [df_sort_5_10_gen, df_sort_5_15_gen, df_sort_5_15_ll, df_sort_6_1_gen]
                    for df_with_one_kernel in dfs:
                        try:
                            df_with_one_kernel[['numeric_version', 'additional_digits', 'kernel_type']] = df_with_one_kernel['Ядро'].str.split('-', expand=True)
                            df_with_one_kernel['additional_digits'] = df_with_one_kernel['additional_digits'].astype(int)
                        except ValueError:
                            df_with_one_kernel[['numeric_version', 'additional_digits', 'kernel_type', 'minor_version']] = np.nan
                        df_with_one_kernel['minor_version'] = df_with_one_kernel['numeric_version'].astype(str).str.split(".").str[-1].astype(int)

                        max_minor_value = df_with_one_kernel['minor_version'].max()
                        df_sort_by_minor_version = df_with_one_kernel[df_with_one_kernel['minor_version'] == max_minor_value]
                        max_add_digit_value = df_sort_by_minor_version['additional_digits'].max()
                        df_sort_by_minor_version_and_add_digit = df_sort_by_minor_version[df_sort_by_minor_version['additional_digits'] == max_add_digit_value]
                        df_sort_by_minor_version_and_add_digit = df_sort_by_minor_version_and_add_digit.drop(['numeric_version', 'additional_digits', 'kernel_type', 'minor_version'], axis=1)
                        new_df_temp = new_df_temp.append(df_sort_by_minor_version_and_add_digit)
                    if "1.7" not in df_for_each_version['Релиз'].iloc[0]:
                        new_df_temp = new_df_temp.append(df_for_each_version)
                # ###
                df = new_df_temp
                df = df.sort_values(by=['Режим защищенности', 'Релиз'], ascending=[True, True])
                panda_series = df['rating_2']
                data_for_df[key]['rating'] = panda_series.tolist()
                
                df_5_10 = df[df["Ядро"].str.startswith('5.10')]
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
                file_html = open(f"{stat_dir}/{test_name}_{key}_1.html", "w")
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
                new_file_html = open(f"{stat_dir}/{test_name}_{key}_2.html", 'w')
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
                    if temp < np.mean(data_ratings.get('rating')) - 1.5 * np.std(data_ratings.get('rating')):
                        colors.append("#ea5c76")
                    elif temp > np.mean(data_ratings.get('rating')) + 1.5 * np.std(data_ratings.get('rating')):
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
                ax.set_title(f"Сравнительная диаграмма значений рейтингов, \nвычисленных на основании результатов нагрузочного тестирования. \n {grade}_{key}\n{test_name}")
                for i, val in enumerate(data_ratings.get("rating")):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                    plt.text(i + 1, val * 0.5, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
                red_patch = mpatches.Patch(color='#ea5c76', label='Рейтинг ниже мат. ожидания на величину x1.5 превышающую стандартное отклонение')
                green_patch = mpatches.Patch(color='#c7d84c', label='Рейтинг соответвует доверительному интервалу')
                yellow_patch = mpatches.Patch(color='#ffc322', label='Рейтинг выше мат. ожидания на величину x1.5 превышающую стандартное отклонение')
                ax.legend(handles=[red_patch, green_patch, yellow_patch])
                fig.savefig(f"{stat_dir}/{test_name}_statistics_{key}.png")


        def create_comparison_kernel_graph(temp_data_kernel, test_name):
            for kernel, temp_data_frame in temp_data_kernel.items():
                for data_kernel in temp_data_frame:
                    fig, ax = plt.subplots(figsize=(12.8, 7.2))
                    ax.bar(data_kernel['Релиз'], data_kernel['rating_2'], color="#a3d1cd")
                    if data_kernel.empty:
                        continue
                    grade = self.get_grade(list(data_kernel['Стенд'])[0])
                    ax.set_title(f"Сводная диаграмма сравнения по ядрам.\n{grade}_{list(data_kernel['Стенд'])[0]} - {kernel}\n{test_name}")
                    for i, val in enumerate(data_kernel['rating_2']):
                        try:
                            val = int(val)
                        except ValueError:
                            pass
                        plt.text(i, val * 0.5, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
                    fig.savefig(f"{stat_dir}/{test_name}_statistics_{list(data_kernel['Стенд'])[0]}_{kernel}.jpg")
            

        def create_comparison_kernel_line_graph(temp_data_kernel, test_name):
            dataframes = [p[0] for p in temp_data_kernel.values()]
            merged_df = reduce(lambda left, right: pd.merge(left, right, on=["Релиз", "Стенд"], how='outer'), dataframes)
            ratings_for_plt_graph = merged_df.iloc[::, 3::2]
            fig, ax = plt.subplots(figsize=(12.8, 7.2))
            ax.grid(True, alpha=.6)
            ax.set_title(f"Линейная диаграмма сравнения по ядрам.\n{test_name}")
            colors = ['#f90829', '#007b7a', '#f9b312', '#c7d84c']
            for index in range(ratings_for_plt_graph.shape[1]):
                ax.plot(merged_df['Релиз'], ratings_for_plt_graph.iloc[::, index], "o-", color=colors[index])
            plt.legend(temp_data_kernel.keys())

            # Lighten borders
            plt.gca().spines["top"].set_alpha(.0)
            plt.gca().spines["bottom"].set_alpha(.3)
            plt.gca().spines["right"].set_alpha(.0)
            plt.gca().spines["left"].set_alpha(.3)

            plt.savefig(f"{stat_dir}/{test_name}_statistics_kernels_all.jpg")

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
                if not merged_df.empty and merged_df.columns[0] == "Релиз":
                    ratings_for_plt_graph = merged_df.iloc[::, 3::2]
                    # names_stand = merged_df.iloc[::, 2::2].mode().iloc[0].tolist()
                    mode_df = merged_df.iloc[:, 2::2].mode()
                    if not mode_df.empty:
                        names_stand = mode_df.iloc[0].dropna().tolist()
                        grades = list(map(self.get_grade, names_stand))
                        grades_with_stands = list(map(lambda x, y: str(x) + "_" + str(y), grades, names_stand))
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

                        plt.savefig(f"{stat_dir}/{test_name}_statistics_all_stands_{title}_kernel.jpg")
        
        def create_summary_table(dfs1, dfs2):
            merged_dataframes = []
            for stand, df in dfs2.items():
                if stand in dfs1.keys():
                    df_temp = pd.merge(dfs1[stand], dfs2[stand], how='outer', left_on=["Релиз", "Ядро", "Стенд"], right_on=["Релиз", "Ядро", "Стенд"])
                    merged_dataframes.append(df_temp)
            return merged_dataframes
        
        def create_summary_graph(merged_df, legend):
            for df in merged_df:
                bar_width = 0.3
                # shcala_x = [x for x in range(len(df['Релиз']))]
                
                fig, ax = plt.subplots(figsize=(16, 9))
                grade = self.get_grade(df['Стенд'].mode()[0])
                ax.set_title(f"Сравнительная диаграмма значений рейтингов PSQL {legend[0]}/{legend[1]}.\n{grade}_{df['Стенд'].mode()[0]}")
                # ax.grid(True, alpha=.3)
                ax.set_ylabel("Значение рейтинга")
                if max(df['rating_2_x'].fillna(0)) > max(df['rating_2_y'].fillna(0)):
                    ax.set_ylim([0, max(df['rating_2_x'].fillna(0)) + max(df['rating_2_x'].fillna(0)) * 0.2])
                else:
                    ax.set_ylim([0, max(df['rating_2_y'].fillna(0)) + max(df['rating_2_y'].fillna(0)) * 0.2])
                df['version'] = df['Релиз'] + "_" + df['Ядро']
                shcala_x = np.array([x for x in range(len(df['Релиз']))])
                
                # shcala_x = np.array([x for x in range(1, len(df['version']) + 1, 1)])
                # print(shcala_x)
                ax.bar(shcala_x - bar_width / 2, df['rating_2_x'], color='#88c1f2', alpha=0.8, width=bar_width)
                ax.bar(shcala_x + bar_width / 2, df['rating_2_y'], color='#ea5c76', alpha=0.8, width=bar_width)
                plt.xticks(ticks=shcala_x, labels=df['version'], rotation=20, horizontalalignment='right')
                # plt.gca().set_xticklabels(df['version'], rotation=20, horizontalalignment='right')
                
                for i, val in enumerate(df['rating_2_x']):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                    if val != 0:
                        # plt.text(i, val, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
                        plt.text(i - bar_width / 2, val * 0.5, val, rotation=90, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
                for i, val in enumerate(df['rating_2_y']):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                    if val != 0:
                        # plt.text(i, val * 0.5, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
                         plt.text(i + bar_width / 2, val * 0.5, val, rotation=90, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
                ax.legend(legend)
                plt.savefig(f"{stat_dir}/{legend[0]}-{legend[1]}_graph_{df['Стенд'].mode()[0]}_summ.jpg")
                
        
        def create_balance_graph(data_for_df, index, column):
            title_graph = {
                1: "Линейная диаграмма сравнения неудачных запросов (количество)",
                2: "Линейная диаграмма сравнения неудачных запросов (проценты)"
            }
            temp_data_for_graph = {}
            for key, data in data_for_df.items():
                if len(data.get("data")) == 0:
                    continue
                old_df = pd.DataFrame(data=data.get("data"),
                                      columns=['Релиз', 'Ядро', 'Режим защищенности', 'Стенд', 'Количество неудачных запросов 1', 'Процент неудачных запросов 1', 'Количество неудачных запросов', 'Процент неудачных запросов'], 
                                      index=np.arange(1, len(data.get("data")) + 1))
                df = old_df.sort_values(by=['Режим защищенности', 'Релиз'], ascending=[True, True])
                """
                    Строим HTML
                """
                df_for_table = df.drop(['Количество неудачных запросов 1', 'Процент неудачных запросов 1'], axis=1)
                statistics_table_html = df_for_table.to_html(escape=False, index=False)
                file_html = open(f"{stat_dir}/table_balance_{key}_1.html", "w")
                file_html.writelines('<h1><a href="https://life.astralinux.ru/pages/viewpage.action?pageId=192234259">Описание стендов нагрузочного тестирования</a></h1>')
                file_html.writelines(f"<h1>Сводная таблица результатов тестирования</h1> {statistics_table_html}")
                file_html.close()

                temp_data_for_graph[key] = (df['Релиз'] + "_" + df['Ядро'])
                fig, ax = plt.subplots(figsize=(16, 9))
                ax.grid(True, alpha=.6)
                ax.set_title(f"{title_graph[index + 1]}\n")
                ax.set_ylabel(column.strip(" 1"))
                df[column] = pd.to_numeric(df[column], errors='coerce')
                min_val = min(df[column])
                max_val = max(df[column])
                # print(max_val, max_val + max_val * 0.01)
                # print(min_val, min_val - min_val * 0.01)
                ax.set_ylim([min_val - min_val * 0.2, max_val + max_val * 0.2])
                shcala_text = temp_data_for_graph[key]
                shcala = [x for x in range(1, len(df) + 1, 1)]
                ax.plot(shcala, df[column], "o-", color='#ea5c76')
                ax.set_xticks(shcala)
                plt.gca().set_xticklabels(shcala_text, rotation=20, horizontalalignment='right')
                # Lighten borders
                plt.gca().spines["top"].set_alpha(.0)
                plt.gca().spines["bottom"].set_alpha(.3)
                plt.gca().spines["right"].set_alpha(.0)
                plt.gca().spines["left"].set_alpha(.3)
                
                fig.savefig(f"{stat_dir}/balance_{index+1}_statistics_{key}.png")
                
                

                
        
        data_df_orel = collect_data(test_name="postgresql")
        tmp_data_for_gr, tmp_data_krnl, df_psql = build_dataframes(data_for_df=data_df_orel, test_name="postgresql")
        create_graphs(data_for_df=data_df_orel, test_name="postgresql", temp_data_for_graph=tmp_data_for_gr)
        
            
        data_df_smolensk = collect_data(test_name="postgresql-sm")
        tmp_data_for_gr_smol, tmp_data_krnl_smol, df_psql_sm = build_dataframes(data_for_df=data_df_smolensk, test_name="postgresql-sm")
        create_graphs(data_for_df=data_df_smolensk, test_name="postgresql-sm", temp_data_for_graph=tmp_data_for_gr_smol)

        data_df_orel_audit_off = collect_data(test_name="postgresql-aud-off")
        tmp_data_for_gr_audit_off, tmp_data_krnl_audit_off, df_psql_audit_off = build_dataframes(data_for_df=data_df_orel_audit_off, test_name="postgresql-aud-off")
        create_graphs(data_for_df=data_df_orel_audit_off, test_name='postgresql-aud-off', temp_data_for_graph=tmp_data_for_gr_audit_off)

        data_df_parsec = collect_data(test_name="psql parsec")
        tmp_data_for_gr_parsec, tmp_data_krnl_parsec, df_psql_parsec = build_dataframes(data_for_df=data_df_parsec, test_name="psql-parsec")
        create_graphs(data_for_df=data_df_parsec, test_name="psql-parsec", temp_data_for_graph=tmp_data_for_gr_parsec)

        data_df_vanilla = collect_data(test_name="psql vanilla")
        tmp_data_for_gr_vanilla, tmp_data_krnl_vanilla, df_psql_vanilla = build_dataframes(data_for_df=data_df_vanilla, test_name="psql-vanilla")
        create_graphs(data_for_df=data_df_vanilla, test_name="psql-vanilla", temp_data_for_graph=tmp_data_for_gr_vanilla)

        data_df_tantor_vanilla = collect_data(test_name="tantor vanilla")
        tmp_data_for_gr_tantor_vanilla, tmp_data_kenl_tantor_vanilla, df_tantor_vanilla = build_dataframes(data_for_df=data_df_tantor_vanilla, test_name="tantor-vanilla")
        create_graphs(data_for_df=data_df_tantor_vanilla, test_name="tantor-vanilla", temp_data_for_graph=tmp_data_for_gr_tantor_vanilla)


        summ_df = create_summary_table(dfs1=df_psql, dfs2=df_psql_sm)
        create_summary_graph(merged_df=summ_df, legend=["Orel", "Smolensk"])

        summ_df_orel_and_orel_aud_off = create_summary_table(dfs1=df_psql, dfs2=df_psql_audit_off)
        create_summary_graph(merged_df=summ_df_orel_and_orel_aud_off, legend=["Orel", "Orel-audit-off"])

        summ_df_orel_and_parsec = create_summary_table(dfs1=df_psql, dfs2=df_psql_parsec)
        create_summary_graph(merged_df=summ_df_orel_and_parsec, legend=["Orel", "Parsec"])

        summ_df_orel_and_vanilla = create_summary_table(dfs1=df_psql, dfs2=df_psql_vanilla)
        create_summary_graph(merged_df=summ_df_orel_and_vanilla, legend=["Orel", "Vanilla"])

        create_comparison_kernel_graph(temp_data_kernel=tmp_data_krnl, test_name="postgresql")
        # create_comparison_kernel_and_stand_graph(temp_data_kernel=tmp_data_krnl, test_name="postgresql")
        create_comparison_kernel_line_graph(temp_data_kernel=tmp_data_krnl, test_name="postgresql")
                
        data_df_balance = collect_data(test_name="psql balance")
        for ind, column in enumerate(['Количество неудачных запросов 1', 'Процент неудачных запросов 1']):
            create_balance_graph(data_for_df=data_df_balance, index=ind, column=column)

        


    """
        Создаем итоговую html страницу для life
    """
    def upload_statistics(self, type_stat='PostgreSQL', rc=None, pp_title=None):
        if rc and pp_title:
            version_key = pp_title.split(" ⬝ ")[1]
            main_stat_dir = 'statistics_rc'
            stat_dir = f"{main_stat_dir}/{version_key}"
            if not version_key in os.listdir(main_stat_dir):
                os.mkdir(stat_dir)
        else:
            stat_dir = "statistics"
        confluence_stat = StatisticsToConfluence(username=self.username, token=self.token)
        if rc:
            parent_page = pp_title
            page_rc_title = version_key
        else:
            parent_page = "Статистика"
            page_rc_title = ""
        
        confluence_stat.create_confluence_page(page_space=SPACE, page_title=f"Статистика.{page_rc_title} {type_stat}", parent_page_title=parent_page)

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

        image_list, images_list_smolensk, image_list_aud_off, image_list_parsec, image_list_vanilla, image_list_tantor_vanilla, image_list_balance = [], [], [], [], [], [], []
        table_with_data_list, table_with_data_list_smolensk, table_with_data_list_aud_off, table_with_data_list_parsec, table_with_data_list_vanilla, table_with_data_list_tantor_vanilla, table_balance_data_list = [], [], [], [], [], [], []
        table_with_mat_stat_list, table_with_mat_stat_list_smolensk, table_with_mat_stat_list_aud_off, table_with_mat_stat_list_parsec, table_with_mat_stat_list_vanilla, table_with_mat_stat_list_tantor_vanilla = [], [], [], [], [], []
        # kernel_image_list = [[], [], []]
        
        # new_kernel_image_dict = {
        #     'stand1': [[], [], []],
        #     'stand2': [[], [], []],
        #     'stand3': [[], [], []],
        #     'stand4': [[], [], []] 
        # }

        new_kernel_image_dict = {
            # 'stand1': [],
            # 'stand2': [],
            # 'stand3': [],
            # 'stand4': [] 
        }
        
        lst_all_stands_stat_kernel = []

        summ_graphs_list, summ_graphs_list_aud_on_off, summ_graphs_list_orel_and_parsec, summ_graphs_list_orel_and_vanilla  = [], [], [], []

        header_orel, header_smolensk, header_parsec, header_vanilla, header_tantor_vanilla, header_orel_vs_smolensk, header_aud_off, header_orel_and_parsec, header_orel_and_vanilla, header_aud_on_vs_off, header_balance = [], [], [], [], [], [], [], [], [], [], []
        
        for file in sorted(os.listdir(f"{stat_dir}")):
            if file.endswith("png"):
                confluence_stat.attache_files(file=f'{stat_dir}/{file}', page_space=SPACE, page_title=f"Статистика.{page_rc_title} {type_stat}")
                img = template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"),
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
                elif file.startswith("psql-parsec"):
                    image_list_parsec.append(img)
                    header_parsec.append(name_stand)
                elif file.startswith("psql-vanilla"):
                    image_list_vanilla.append(img)
                    header_vanilla.append(name_stand)
                elif file.startswith("tantor-vanilla"):
                    image_list_tantor_vanilla.append(img)
                    header_tantor_vanilla.append(name_stand)
                elif file.startswith("balance"):
                    image_list_balance.append(img)
                    header_balance.append(name_stand)
                else:
                    image_list.append(img)
                    header_orel.append(name_stand)

            if file.endswith("1.html"):
                file_table = open(f'{stat_dir}/{file}', 'r')
                table = file_table.read()
                file_table.close()
                if file.startswith("postgresql-sm"):
                    table_with_data_list_smolensk.append(table)
                elif file.startswith('postgresql-aud-off'):
                    table_with_data_list_aud_off.append(table)
                elif file.startswith("postgresql-vo"):
                    pass
                elif file.startswith("psql-parsec"):
                    table_with_data_list_parsec.append(table)
                elif file.startswith("psql-vanilla"):
                    table_with_data_list_vanilla.append(table)
                elif file.startswith("tantor-vanilla"):
                    table_with_data_list_tantor_vanilla.append(table)
                elif file.startswith("table_balance"):
                    table_balance_data_list.append(table)
                else:
                    table_with_data_list.append(table)

            if file.endswith("2.html"):
                new_file_table = open(f'{stat_dir}/{file}', 'r')
                mat_stat_table = new_file_table.read()
                new_file_table.close()
                if file.startswith("postgresql-sm"):
                    table_with_mat_stat_list_smolensk.append(mat_stat_table)
                elif file.startswith('postgresql-aud-off'):
                    table_with_mat_stat_list_aud_off.append(mat_stat_table)
                elif file.startswith("postgresql-vo"):
                    pass
                elif file.startswith("psql-parsec"):
                    table_with_mat_stat_list_parsec.append(mat_stat_table)
                elif file.startswith("psql-vanilla"):
                    table_with_mat_stat_list_vanilla.append(mat_stat_table)
                elif file.startswith("tantor-vanilla"):
                    table_with_mat_stat_list_tantor_vanilla.append(mat_stat_table)
                else:
                    table_with_mat_stat_list.append(mat_stat_table)

            stand_name_temp_for_key = file.split("_")[-2]
            if stand_name_temp_for_key not in new_kernel_image_dict.keys():
                    new_kernel_image_dict[stand_name_temp_for_key] = []
            templ_img = template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"), img_png=file)    
            if file.endswith("5.10.jpg"):  
                new_kernel_image_dict[stand_name_temp_for_key].append(templ_img)
                confluence_stat.attache_files(file=f'{stat_dir}/{file}', page_space=SPACE, page_title=f"Статистика.{page_rc_title} {type_stat}")
                # kernel_image_list[0].append(template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"),
                #                                                 img_png=file))
            if file.endswith("5.15-gen.jpg"):
                new_kernel_image_dict[stand_name_temp_for_key].append(templ_img)
                confluence_stat.attache_files(file=f'{stat_dir}/{file}', page_space=SPACE, page_title=f"Статистика.{page_rc_title} {type_stat}")
                # kernel_image_list[1].append(template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"),
                #                                                 img_png=file))
            if file.endswith("5.15-ll.jpg"):
                new_kernel_image_dict[stand_name_temp_for_key].append(templ_img)
                confluence_stat.attache_files(file=f'{stat_dir}/{file}', page_space=SPACE, page_title=f"Статистика.{page_rc_title} {type_stat}")
                # kernel_image_list[2].append(template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"),
                #                                                 img_png=file))
            # if file.endswith("kernel.jpg"):
            #     confluence_stat.attache_files(file=f'{stat_dir}/{file}', page_space=SPACE, page_title=f"Статистика.{page_rc_title} {type_stat}")
            #     lst_all_stands_stat_kernel.append(template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"),
            #                                                           img_png=file))
            if file.endswith("kernels_all.jpg"):
                confluence_stat.attache_files(file=f'{stat_dir}/{file}', page_space=SPACE, page_title=f"Статистика.{page_rc_title} {type_stat}")
                lst_all_stands_stat_kernel.append(template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"),
                                                                      img_png=file))
            
            if file.endswith("summ.jpg"):
                name_stand = file.split("_")[2]
                confluence_stat.attache_files(file=f'{stat_dir}/{file}', page_space=SPACE, page_title=f"Статистика.{page_rc_title} {type_stat}")
                if file.startswith("Orel-Smolensk"):
                    summ_graphs_list.append(template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"), 
                                                                img_png=file))
                    header_orel_vs_smolensk.append(name_stand)
                elif file.startswith("Orel-Orel-audit-off"):
                    summ_graphs_list_aud_on_off.append(template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"), 
                                                                           img_png=file))
                    header_aud_on_vs_off.append(name_stand)
                    # print(header_aud_on_vs_off)
                elif file.startswith("Orel-Parsec"):
                    summ_graphs_list_orel_and_parsec.append(template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"), 
                                                                                img_png=file))
                    header_orel_and_parsec.append(name_stand)
                elif file.startswith("Orel-Vanilla"):
                    summ_graphs_list_orel_and_vanilla.append(template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"), 
                                                                                 img_png=file))
                    header_orel_and_vanilla.append(name_stand)
                else:
                    pass                            
                
                
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
        # nav_lst_orel, nav_lst_smolensk, nav_lst_orel_vs_smolensk, nav_lst_aud_off, nav_lst_aud_on_off, nav_lst_parsec, nav_lst_vanilla, nav_lst_tantor_vanilla, nav_lst_orel_and_parsec, nav_lst_orel_and_vanilla = [], [], [], [], [], [], [], [], [], []
        # nav_body = '''
        #     <li><a href="#id-Статистика.{rc_title}PostgreSQL-Orel">Orel</a>
        #         <ul>
        #             {list_orel}
        #         </ul>
        #     </li>
        #     <li><a href="#id-Статистика.{rc_title}PostgreSQL-Smolensk">Smolensk</a>
        #         <ul>
        #             {list_smolensk}
        #         </ul>
        #     </li>
        #     <li><a href="#id-Статистика.{rc_title}PostgreSQL-Orelauditoff">Orel audit off</a>
        #         <ul>
        #             {list_aud_off}
        #         </ul>
        #     </li>
        #     <li><a href="#id-Статистика.{rc_title}PostgreSQL-OrelvsSmolensk">Orel vs Smolensk</a>
        #         <ul>
        #             {list_orel_vs_smolensk}
        #         </ul>
        #     </li>
        #     <li><a href="#id-Статистика.{rc_title}PostgreSQL-OrelvsOrelauditoff">Orel vs Orel audit off</a>
        #         <ul>
        #             {list_aud_on_off}
        #         </ul>
        #     </li>
        #     <li><a href="#id-Статистика.{rc_title}PostgreSQL-Parsec">Parsec</a>
        #         <ul>
        #             {list_parsec}
        #         </ul>
        #     </li>
        #     <li><a href="#id-Статистика.{rc_title}PostgreSQL-Vanilla">Vanilla</a>
        #         <ul>
        #             {list_vanilla}
        #         </ul>
        #     </li>
        #     <li><a href="#id-Статистика.{rc_title}PostgreSQL-Tantorvanilla">Tantor vanilla</a>
        #         <ul>
        #             {list_tantor_vanilla}
        #         </ul>
        #     </li>
        #     <li><a href="#id-Статистика.{rc_title}PostgreSQL-OrelvsParsec">Orel vs Parsec</a>
        #         <ul>
        #             {list_orel_vs_parsec}
        #         </ul>
        #     </li>
        #     <li><a href="#id-Статистика.{rc_title}PostgreSQL-OrelvsVanilla">Orel vs Vanilla</a>
        #         <ul>
        #             {list_orel_vs_vanilla}
        #         </ul>
        #     </li>    
        # '''

        nav_body_new = '''
            <li><a href="#id-Статистика.{rc_title}PostgreSQL-Orel">Orel</a></li>
            <li><a href="#id-Статистика.{rc_title}PostgreSQL-Smolensk">Smolensk</a></li>
            <li><a href="#id-Статистика.{rc_title}PostgreSQL-Orelauditoff">Orel audit off</a></li>
            <li><a href="#id-Статистика.{rc_title}PostgreSQL-OrelvsSmolensk">Orel vs Smolensk</a></li>
            <li><a href="#id-Статистика.{rc_title}PostgreSQL-OrelvsOrelauditoff">Orel vs Orel audit off</a></li>
            <li><a href="#id-Статистика.{rc_title}PostgreSQL-Parsec">Parsec</a></li>
            <li><a href="#id-Статистика.{rc_title}PostgreSQL-Vanilla">Vanilla</a></li>
            <li><a href="#id-Статистика.{rc_title}PostgreSQL-Tantorvanilla">Tantor vanilla</a></li>
            <li><a href="#id-Статистика.{rc_title}PostgreSQL-OrelvsParsec">Orel vs Parsec</a></li>
            <li><a href="#id-Статистика.{rc_title}PostgreSQL-OrelvsVanilla">Orel vs Vanilla</a></li>
            <li><a href="#id-Статистика.{rc_title}PostgreSQL-Balance">Balance</a></li>    
        '''

        html_list.append('<hr/><h1 style="text-align: center;">Orel</h1>')
        for item in lst_all_stands_stat_kernel:
            """
                TODO требуется доработка для RC 
            """
            html_list.append(item)

        if len(table_with_data_list) > 0:
            for ind, item in enumerate(table_with_data_list):
                grade = self.get_grade(header_orel[ind])
                # nav_lst_orel.append(f'<li><a href="#id-Статистика.{page_rc_title}PostgreSQL-{grade}_{header_orel[ind]}">{grade}_{header_orel[ind]}</a></li>')
                html_list.append(f"<hr/><h1>{grade}_{header_orel[ind]}</h1>")
                html_list.append(image_list[ind])
                html_list.append(item)
                html_list.append("<h1>Таблица основных статистических параметров.</h1>" + "<br/>" + table_with_mat_stat_list[ind])
                """
                    Добавление картинок сравнения по ядром в основную HTML 3 ядра, поэтому range 3. см как задается kernel_image_list
                """
                # if not rc:
                #     for i in range(3):
                #         try:
                #             html_list.append(kernel_image_list[i][ind])
                #         except IndexError:
                #             pass
                # else:
                for i in range(3):
                    try:
                        html_list.append(new_kernel_image_dict[header_orel[ind]][i])
                    except IndexError:
                        pass
                    
        # if not rc:
        if len(table_with_data_list_smolensk) > 0:
            html_list.append('<h1 style="text-align: center;">Smolensk</h1>')
            for ind, item in enumerate(table_with_data_list_smolensk):
                grade = self.get_grade(header_smolensk[ind])
                # nav_lst_smolensk.append(f'<li><a href="#id-Статистика.{page_rc_title}PostgreSQL-{grade}_{header_smolensk[ind]}.1">{grade}_{header_smolensk[ind]}</a></li>')
                html_list.append(f"<hr/><h1>{grade}_{header_smolensk[ind]}</h1>")
                html_list.append(images_list_smolensk[ind])
                html_list.append(item)
                html_list.append("<h1>Таблица основных статистических параметров.</h1>" + "<br/>" + table_with_mat_stat_list_smolensk[ind])

        if len(table_with_data_list_aud_off) > 0:
            html_list.append('<h1 style="text-align: center;">Orel audit off</h1>')
            for ind, item in enumerate(table_with_data_list_aud_off):
                grade = self.get_grade(header_aud_off[ind])
                # nav_lst_aud_off.append(f'<li><a href="#id-Статистика.{page_rc_title}PostgreSQL-{grade}_{header_aud_off[ind]}.2">{grade}_{header_aud_off[ind]}</a></li>')
                html_list.append(f"<hr/><h1>{grade}_{header_aud_off[ind]}</h1>")
                html_list.append(image_list_aud_off[ind])
                html_list.append(item)
                html_list.append("<h1>Таблица основных статистических параметров.</h1>" + "<br/>" + table_with_mat_stat_list_aud_off[ind])

        # if not rc:
        if len(summ_graphs_list) > 0:
            html_list.append('<h1 style="text-align: center;">Orel vs Smolensk</h1>')
            for ind, item in enumerate(summ_graphs_list):
                grade = self.get_grade(header_orel_vs_smolensk[ind])
                # nav_lst_orel_vs_smolensk.append(f'<li><a href="#id-Статистика.{page_rc_title}PostgreSQL-{grade}_{header_orel_vs_smolensk[ind]}.3">{grade}_{header_orel_vs_smolensk[ind]}</a></li>')
                html_list.append(f"<hr/><h1>{grade}_{header_orel_vs_smolensk[ind]}</h1>")
                html_list.append(item)

        if len(summ_graphs_list_aud_on_off) > 0:
            html_list.append('<h1 style="text-align: center;">Orel vs Orel audit off</h1>')
            for ind, item in enumerate(summ_graphs_list_aud_on_off):
                grade = self.get_grade(header_aud_on_vs_off[ind])
                # print(f'<li><a href="#id-Статистика.PostgreSQL-{grade}_{header_aud_on_vs_off[ind]}.4">{grade}_{header_aud_on_vs_off[ind]}</a></li>')
                # nav_lst_aud_on_off.append(f'<li><a href="#id-Статистика.{page_rc_title}PostgreSQL-{grade}_{header_aud_on_vs_off[ind]}.4">{grade}_{header_aud_on_vs_off[ind]}</a></li>')
                html_list.append(f"<hr/><h1>{grade}_{header_aud_on_vs_off[ind]}</h1>")
                html_list.append(item)
        
        if len(table_with_data_list_parsec) > 0:
            html_list.append('<h1 style="text-align: center;">Parsec</h1>')
            for ind, item in enumerate(table_with_data_list_parsec):
                grade = self.get_grade(header_parsec[ind])
                # nav_lst_parsec.append(f'<li><a href="#id-Статистика.{page_rc_title}PostgreSQL-{grade}_{header_parsec[ind]}.5">{grade}_{header_parsec[ind]}</a></li>')
                html_list.append(f"<hr/><h1>{grade}_{header_parsec[ind]}</h1>")
                html_list.append(image_list_parsec[ind])
                html_list.append(item)
                html_list.append("<h1>Таблица основных статистических параметров.</h1>" + "<br/>" + table_with_mat_stat_list_parsec[ind])
        
        if len(table_with_data_list_vanilla) > 0:
            html_list.append('<h1 style="text-align: center;">Vanilla</h1>')
            for ind, item in enumerate(table_with_data_list_vanilla):
                grade = self.get_grade(header_vanilla[ind])
                # nav_lst_vanilla.append(f'<li><a href="#id-Статистика.{page_rc_title}PostgreSQL-{grade}_{header_vanilla[ind]}.6">{grade}_{header_vanilla[ind]}</a></li>')
                html_list.append(f"<hr/><h1>{grade}_{header_vanilla[ind]}</h1>")
                html_list.append(image_list_vanilla[ind])
                html_list.append(item)
                html_list.append("<h1>Таблица основных статистических параметров.</h1>" + "<br/>" + table_with_mat_stat_list_vanilla[ind])
        
        if len(table_with_data_list_tantor_vanilla) > 0:
            html_list.append('<h1 style="text-align: center;">Tantor vanilla</h1>')
            for ind, item in enumerate(table_with_data_list_tantor_vanilla):
                grade = self.get_grade(header_tantor_vanilla[ind])
                # nav_lst_tantor_vanilla.append(f'<li><a href="#id-Статистика.{page_rc_title}PostgreSQL-{grade}_{header_tantor_vanilla[ind]}.7">{grade}_{header_tantor_vanilla[ind]}</a></li>')
                html_list.append(f"<hr/><h1>{grade}_{header_tantor_vanilla[ind]}</h1>")
                html_list.append(image_list_tantor_vanilla[ind])
                html_list.append(item)
                html_list.append("<h1>Таблица основных статистических параметров.</h1>" + "<br/>" + table_with_mat_stat_list_tantor_vanilla[ind])

        if len(summ_graphs_list_orel_and_parsec) > 0:
            html_list.append('<h1 style="text-align: center;">Orel vs Parsec</h1>')
            for ind, item in enumerate(summ_graphs_list_orel_and_parsec):
                grade = self.get_grade(header_orel_and_parsec[ind])
                # nav_lst_orel_and_parsec.append(f'<li><a href="#id-Статистика.{page_rc_title}PostgreSQL-{grade}_{header_orel_and_parsec[ind]}.8">{grade}_{header_orel_and_parsec[ind]}</a></li>')
                html_list.append(f"<hr/><h1>{grade}_{header_orel_and_parsec[ind]}</h1>")
                html_list.append(item)
        
        if len(summ_graphs_list_orel_and_vanilla) > 0:
            html_list.append('<h1 style="text-align: center;">Orel vs Vanilla</h1>')
            for ind, item in enumerate(summ_graphs_list_orel_and_vanilla):
                grade = self.get_grade(header_orel_and_vanilla[ind])
                # nav_lst_orel_and_vanilla.append(f'<li><a href="#id-Статистика.{page_rc_title}PostgreSQL-{grade}_{header_orel_and_vanilla[ind]}.9">{grade}_{header_orel_and_vanilla[ind]}</a></li>')
                html_list.append(f"<hr/><h1>{grade}_{header_orel_and_vanilla[ind]}</h1>")
                html_list.append(item)
                
        if len(image_list_balance) > 0:
            html_list.append('<h1 style="text-align: center;">Balance</h1>')
            for ind, item in enumerate(image_list_balance):
                # grade = self.get_grade(header_balance[ind])
                html_list.append(item)
        if len(table_balance_data_list) > 0:
            for ind, item in enumerate(table_balance_data_list):
                html_list.append(item)


        # nav = nav_start + nav_body.format(rc_title=page_rc_title, list_orel="".join(nav_lst_orel), list_smolensk="".join(nav_lst_smolensk), list_orel_vs_smolensk="".join(nav_lst_orel_vs_smolensk), list_aud_off="".join(nav_lst_aud_off), list_parsec="".join(nav_lst_parsec), list_vanilla="".join(nav_lst_vanilla), list_tantor_vanilla="".join(nav_lst_tantor_vanilla), list_orel_vs_parsec="".join(nav_lst_orel_and_parsec), list_orel_vs_vanilla="".join(nav_lst_orel_and_vanilla), list_aud_on_off="".join(nav_lst_aud_on_off)) + nav_end
        nav_new = nav_start + nav_body_new.format(rc_title=page_rc_title) + nav_end

        html_list.insert(0, nav_new)

        html_page = "".join(html_list)

        confluence_stat.update_confluence_page(page_space=SPACE, page_title=f"Статистика.{page_rc_title} {type_stat}", page_body=html_page)

    def update_statistics(self):
        pages, rc_pages = self.get_list_required_pages()
        print(rc_pages)
        columns = ["Релиз", "Ядро", "Режим защищенности", "Стенд", "Рейтинг", 'rating_2']

        self.get_info_from_pages(pages=pages, columns_df=columns)
        for key, value in rc_pages.items():
            if value:
                self.get_info_from_pages(pages=value, columns_df=columns, rc=True, version_key=key.split(" ⬝ ")[1])
        self.upload_statistics()
        for key, value in rc_pages.items():
            if value:
                self.upload_statistics(rc=True, pp_title=key)


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