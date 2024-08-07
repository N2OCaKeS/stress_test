import os
import re
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
# from virt_conf import CONFLUENCE_URL
from bs4 import BeautifulSoup
from atlassian import Confluence
from distutils.version import LooseVersion

CONFLUENCE_URL = "life.astralinux.ru"

class ConfluencePage:
    __url = f'https://{CONFLUENCE_URL}'

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
    __url=f'https://{CONFLUENCE_URL}'
    
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

class VirtStatistics:
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
    
    def get_list_required_pages(self, id_root_page):
        """
            Получаем дочерние страницы 1.7: 1.7.1; 1.7.2; 1.7.n...
        """
        children_main_page = self.CP.get_child_page_as_html(id=id_root_page, by_title=False)
        required_pages, required_pages_rc = [], []
        test_dict_rc = {}
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
                    Проверяем есть ли в заголовке Файловые системы и имеются ли дочерние страницы
                """
                if "Qemu/KVM/Libvirt" in page.get("title") and self.CP.get_child_page_as_html(id=page_id):
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
                            if "Qemu/KVM/Libvirt" in self.CP.get_page_as_html(id=item_page).get("title"):
                                temp_arr.append(item_page)

            test_dict_rc[name_page_original] = temp_arr

        return required_pages, test_dict_rc
    
    def get_info_from_pages(self, pages, rc=False, version_key=None):
        if rc and version_key:
            main_stat_dir = 'statistics_rc'
            stat_dir = f"{main_stat_dir}/{version_key}"
            if not version_key in os.listdir(main_stat_dir):
                os.mkdir(stat_dir)
        else:
            stat_dir = "statistics"

        def build_main_dataframe(fs_type, data_fs, stand):
            grade = self.get_grade(stand)
            columns = ['Релиз', 'Ядро', 'Режим защищенности', 'Стенд', 'Рейтинг', 'Рейтинг2']
            df = pd.DataFrame(data=data_fs, columns=columns)
            print(data_fs)
            print(df)

            # df = df.sort_values(by=['Режим защищенности', 'Релиз'], ascending=[True, True])
            df['Sort'] = df['Релиз'].apply(lambda s: [LooseVersion(x) for x in s.split('.', 1)])
            df.sort_values(by='Sort', inplace=True)
            df.drop(columns='Sort', inplace=True)
            df.reset_index(drop=True, inplace=True)
            
            panda_series = df['Рейтинг2']
            df.insert(0, "№", [x for x in range(1, len(panda_series.tolist()) + 1, 1)])
            df = df.drop('Рейтинг2', axis=1)
            
            table = df.to_html(escape=False, index=False)
            f_ext4 = open(f"{stat_dir}/fs_{fs_type}_{grade}_1.html", 'w')
            f_ext4.writelines(f"<h2>Сводная таблица результатов тестирования {fs_type} {grade}</h2> {table}")
            f_ext4.close()

            return panda_series.tolist(), df['Релиз'] + '_' + df['Ядро']
        
        def build_mat_stat_dataframe(fs_type, data_fs, stand):
            grade = self.get_grade(stand)
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
            new_file_html = open(f"{stat_dir}/fs_{fs_type}_{grade}_2.html", 'w')
            new_file_html.write(f'<h2>Таблица основных статистических параметров {fs_type} {grade}</h2> {mat_stat_table_html}')
            new_file_html.close()

        def build_graph(fs_type, stand, rating_fg, shcala_txt):
            grade = self.get_grade(stand)
            colors = []
            
            for temp in rating_fg:
                #if temp < np.mean(data_ratings.get('rating')) - 2 * np.std(data_ratings.get('rating')) or temp > np.mean(data_ratings.get('rating')) + 2 * np.std(data_ratings.get('rating')):
                if temp < np.mean(rating_fg) - 1.5 * np.std(rating_fg):
                    colors.append("#ea5c76")
                elif temp > np.mean(rating_fg) + 1.5 * np.std(rating_fg):
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
            ax.set_title(f"{fs_type}. Сравнительная диаграмма значений рейтингов, \nвычисленных на основании результатов нагрузочного тестирования. \n {grade}")
            for i, val in enumerate(rating_fg):
                try:
                    val = int(val)
                except ValueError:
                    pass
                plt.text(i + 1, val * 0.5, val, horizontalalignment='center', verticalalignment='bottom', fontdict={'fontweight':500})
            red_patch = mpatches.Patch(color='#ea5c76', label='Рейтинг ниже мат. ожидания на величину x1.5 превышающую стандартное отклонение')
            green_patch = mpatches.Patch(color='#c7d84c', label='Рейтинг соответвует доверительному интервалу')
            yellow_patch = mpatches.Patch(color='#ffc322', label='Рейтинг выше мат. ожидания на величину x1.5 превышающую стандартное отклонение')
            ax.legend(handles=[red_patch, green_patch, yellow_patch])
            fig.savefig(f"{stat_dir}/fs_{fs_type}_{grade}_1.png")

        file_system_data = {
            'data': {
                'stand1': {
                    'steal time': [],
                    'FIO': [],
                },
                'stand2': {
                    'steal time': [],
                    'FIO': [],
                },
                'stand3': {
                    'steal time': [],
                    'FIO': [],
                },
                'stand4': {
                    'steal time': [],
                    'FIO': [],
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
                src_html = self.CP.get_page_as_html(page_space=SPACE, page_title=title)
                data = src_html.get("body").get("view").get("value")
                soup = BeautifulSoup(data, 'lxml')
                temp_data = title.replace(" ", "_").split("_")
                print(temp_data)
            
                if temp_data[1] == "parsec":
                    type_fs, parsec, astra_version, sec_mode, kernel, stand = temp_data[0], temp_data[1], temp_data[2], temp_data[3], temp_data[4], temp_data[5]
                else:
                    parsec = ""
                    type_fs, astra_version, sec_mode, kernel, stand = temp_data[0], temp_data[1], temp_data[2], temp_data[3], temp_data[4]
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
                link = f"https://{CONFLUENCE_URL}/display/{SPACE}/" + title 
                rating_with_link = f'<a href="{link}">{rating}</a>'

                if parsec:
                    temp_key_fs_with_parsec = f'{type_fs}_{parsec}'
                else:
                    temp_key_fs_with_parsec = type_fs

                try:
                    file_system_data['data'][stand][temp_key_fs_with_parsec]. \
                        append([astra_version, kernel, sec_mode, stand, rating_with_link, float(rating)])
                except KeyError:
                    continue

        for key, data in file_system_data['data'].items():
            #print(key, data)
            if data.get("steal time"):
                rating_for_graph, shcl = build_main_dataframe("Steal-time", data.get("steal time"), key)
                build_mat_stat_dataframe("Steal-time", rating_for_graph, key)
                build_graph(fs_type="Steal-time", stand=key, rating_fg=rating_for_graph, shcala_txt=shcl)
            # if data.get("auditd-p"):
                rating_for_graph, shcl = build_main_dataframe("FIO", data.get("FIO"), key)
                build_mat_stat_dataframe("FIO", rating_for_graph, key)
                build_graph(fs_type="FIO", stand=key, rating_fg=rating_for_graph, shcala_txt=shcl)

    """
        Создаем итоговую html страницу для life
    """
    def upload_statistics(self, type_stat='Qemu/KVM/Libvirt', rc=None, pp_title=None):
        pass


    def update_statistics(self):
        pages_17, rc_pages_17 = self.get_list_required_pages(id_root_page="156339086")
        pages_18, rc_pages_18 = self.get_list_required_pages(id_root_page="244154033")
        pages = pages_17 + pages_18
        rc_pages = {**rc_pages_17, **rc_pages_18}
        self.get_info_from_pages(pages=pages)
        for key, value in rc_pages.items():
            if value:
                self.get_info_from_pages(pages=value, rc=True, version_key=key.split(" ⬝ ")[1])
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
    
    stat = VirtStatistics(username=args.USER, token=args.TOKEN)
    stat.update_statistics()
    