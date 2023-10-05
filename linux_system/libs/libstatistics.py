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

SPACE = "DEVQA"

class UnixBenchStatistics:

    def __init__(self, username, token) -> None:
        self.username = username
        self.token = token
        self.CP = ConfluencePage(username=self.username, token=self.token)
        if not "statistics" in os.listdir():
            os.mkdir("statistics")
        if not "statistics_rc" in os.listdir():
            os.mkdir("statistics_rc")


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
        required_pages, required_pages_rc = [], []
        test_dict_rc = {}
        """
            Проходим по всем версиям
        """
        for id_children_from_main_page in children_main_page:
            """
                Получаем ID страниц PostgreSQL, Системные службы, Файловые системы, UnixBench в каждой конкретной версии
            """
            try:
                name_page_original = self.CP.get_page_as_html(id=id_children_from_main_page).get("title")
                name_page = self.CP.get_page_as_html(id=id_children_from_main_page).get("title").split(" ⬝ ")[1]
                # print(name_page)
            except IndexError:
                pass

            astra_version_child_pages = self.CP.get_child_page_as_html(id=id_children_from_main_page, by_title=False)
            temp_arr = []
            """
                Проходим по каждому полученному ID
            """
            for page_id in astra_version_child_pages:
                """
                    Получаем информацию о странице
                """
                page = self.CP.get_page_as_html(id=page_id)
                """
                    Проверяем есть ли в заголовке UnixBench и имеются ли дочерние страницы
                """
                if "UnixBench" in page.get("title") and self.CP.get_child_page_as_html(id=page_id):
                    required_pages.append(page_id)
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
                            if "UnixBench" in self.CP.get_page_as_html(id=item_page).get("title"):
                                temp_arr.append(item_page)

            test_dict_rc[name_page_original] = temp_arr

        return required_pages, test_dict_rc
    
    def get_info_from_pages(self, pages, columns_df, rc=False, version_key=None):
        if rc and version_key:
            main_stat_dir = 'statistics_rc'
            stat_dir = f"{main_stat_dir}/{version_key}"
            if not version_key in os.listdir(main_stat_dir):
                os.mkdir(stat_dir)
        else:
            stat_dir = "statistics"

        def collect_data(test_name="unix"):
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
                            rating = soup.find(string=re.compile("[Tt]otal rating")).strip().split(" ")[2]
                        except:
                            rating = 0
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
                ax.set_title(f"UnixBench. Сравнительная диаграмма значений рейтингов, \nвычисленных на основании результатов нагрузочного тестирования. \n {grade}_{key}")
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

        data_df_orel = collect_data(test_name="unix")
        print(data_df_orel)
        tmp_data_for_gr, tmp_data_krnl, df_psql = build_dataframes(data_for_df=data_df_orel, test_name="unix")
        create_graphs(data_for_df=data_df_orel, test_name="unix", temp_data_for_graph=tmp_data_for_gr)

    """
        Создаем итоговую html страницу для life
    """
    def upload_statistics(self, type_stat='UnixBench', rc=None, pp_title=None):
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

        confluence_stat = StatisticsToConfluence(username=self.username, token=self.token)
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
        image_list = []
        table_with_data_list = []
        table_with_mat_stat_list = []

        for file in sorted(os.listdir(f"{stat_dir}")):
            if file.endswith("png"):
                confluence_stat.attache_files(file=f'{stat_dir}/{file}', page_space=SPACE, page_title=f"Статистика.{page_rc_title} {type_stat}")
                img = template_img.format(page_id=confluence_stat.get_confluence_page_id(SPACE, f"Статистика.{page_rc_title} {type_stat}"),
                                                    img_png=file)
                image_list.append(img)

            if file.endswith("1.html"):
                file_table = open(f'{stat_dir}/{file}', 'r')
                table = file_table.read()
                file_table.close()
                table_with_data_list.append(table)
            if file.endswith("2.html"):
                new_file_table = open(f'{stat_dir}/{file}', 'r')
                mat_stat_table = new_file_table.read()
                new_file_table.close()
                table_with_mat_stat_list.append(mat_stat_table)
            
        html_list = []
        for ind, item in enumerate(table_with_data_list):
            # grade = self.get_grade(header_orel[ind])
            html_list.append(image_list[ind])
            html_list.append(item)
            html_list.append("<h1>Таблица основных статистических параметров.</h1>" + "<br/>" + table_with_mat_stat_list[ind])
        
        html_page = "".join(html_list)

        confluence_stat.update_confluence_page(page_space=SPACE, page_title=f"Статистика.{page_rc_title} {type_stat}", page_body=html_page)
    
    def update_statistics(self):
        pages, rc_pages = self.get_list_required_pages()
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
    
    stat = UnixBenchStatistics(username=args.USER, token=args.TOKEN)
    stat.update_statistics()