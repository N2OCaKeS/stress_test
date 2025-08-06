import requests
import json
import pandas as pd

from datetime import datetime
from shutil import unpack_archive
from atlassian import Confluence
from atlassian import Jira
from bs4 import BeautifulSoup
from os import path, remove


JIRA_URL = 'jira.astralinux.ru'
CONFLUENCE_URL = 'life.astralinux.ru'
GIT_URL = 'git.astralinux.ru'


class ReportToConfluence():
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
        self.report_files_path = '.'

    def unzip_tarfile(self, tar_name, extract_path='.'):
        unpack_archive(tar_name, extract_path)
        self.report_files_path = extract_path+'/report'

    def create_page_body(self):
        pass

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
                               page_body='this page was automatically created<br/><br/>Content:<br/>',):
        macro_body = '''
        <ac:structured-macro ac:name="children">
        <ac:parameter ac:name="all">true</ac:parameter>
        </ac:structured-macro>
        '''
        page_body = page_body + macro_body
        if not self.__confluence.page_exists(space=page_space, title=page_title):
            if self.__confluence.create_page(space=page_space,
                                             title=page_title,
                                             body=page_body,
                                             parent_id=self.__confluence.get_page_id(space=page_space,
                                                                                     title=parent_page_title),
                                             type='page',
                                             representation='storage',
                                             editor='v2'):
                print(f'+++ page {page_title} is ready in space {page_space}, parent page {parent_page_title}')

    def update_confluence_page(self,
                               page_space,
                               page_title,
                               page_body,):
        if self.__confluence.page_exists(space=page_space, title=page_title):
            self.__confluence.update_page(page_id=self.__confluence.get_page_id(space=page_space, title=page_title),
                                          title=page_title,
                                          body=page_body)


class ReportToJira():
    __url=f'https://{JIRA_URL}'

    def __init__(self, username, password=None, token=None):
        self.__username = username
        self.__password = password
        self.__access_token = token

        if self.__password is not None:
            self.__jira = Jira(url=self.__url,
                               username=self.__username,
                               password=self.__password)
        elif self.__access_token is not None:
            self.__jira = Jira(url=self.__url,
                               username=self.__username,
                               token=self.__access_token)

    def add_comment_to_issue(self, issue, text):
        self.__jira.issue_add_comment(issue_key=issue,
                                      comment=text)
        
    def get_issues_from_sprint(self, sprint_id):
        return self.__jira.get_sprint_issues(sprint_id=sprint_id,
                                             start=0,
                                             limit=500)

    def get_comments_from_issues(self, issue_id):
        return self.__jira.issue_get_comments(issue_id=issue_id)
    


class ReportTempo():
    def __init__(self,
                 author_list,
                 jira_user_list,
                 jira_token,
                 month):
        
        self.author_list = author_list 
        self.jira_user_list = jira_user_list
        self.jira_token = jira_token
        self.month = month

    def tempo_parse(self):
        sptint_url = f'https://{JIRA_URL}/rest/tempo-timesheets/4/worklogs/search'
        headers = {
            'Authorization': self.jira_token,
            'Content-Type': 'application/json'
        }

        payload = {
            "from": f"{self.month}-01",
            "to": f"{self.month}-31",
            "teamId": ["7"],
            "includeSubtasks": True
        }

        response = requests.post(sptint_url, headers=headers, data=json.dumps(payload))


        if response.status_code != 200:
            raise Exception(f"Ошибка запроса: {response.text}")

        #print(response.json())
        data = response.json()


        parsed_entries = []
        task_details = []

        for entry in data:
            try:
                dt = datetime.strptime(entry['started'], '%Y-%m-%d %H:%M:%S.%f')
                parsed_entries.append({
                    'worker': self.jira_user_list[entry['worker']],
                    'date': dt.date(),
                    'hours': float(f"{entry['timeSpentSeconds'] / 3600:.2f}") # Преобразуем секунды в часы
                })
                task_details.append({
                    'worker': self.jira_user_list[entry['worker']],
                    'date': dt.date(),
                    'task_key': entry['issue'].get('key') 
                })
            except Exception as e:
                print(f'Error is: {type(e).__name__}\nMessage is: {str(e)}\n')

        df = pd.DataFrame(parsed_entries)
        task_df = pd.DataFrame(task_details)
        
        task_df_grouped = (
            task_df.groupby(['worker', 'date'])
                .agg({'task_key': lambda x: ', '.join(set(x))})  # Объединение всех задач в одну строку
                .reset_index()
        )

        #print(task_df_grouped)

        # Получаем первый и последний день месяца
        first_day = datetime.strptime(self.month, '%Y-%m').replace(day=1)
        last_day = (first_day + pd.offsets.MonthEnd()).normalize()
        full_dates = pd.date_range(start=first_day, end=last_day)

        # Поворот данных с помощью pivot_table
        pivoted_df = df.pivot_table(values='hours', index='date', columns='worker', aggfunc='sum')
        pivoted_task_df = task_df_grouped.pivot_table(values='task_key', index='date', columns='worker', aggfunc=lambda x: ', '.join(x))

        # Форматируем индекс (date) с добавлением дня недели
        pivoted_df.index = pivoted_df.index.map(lambda x: x.strftime('%Y-%m-%d--%a'))
        pivoted_task_df.index = pivoted_task_df.index.map(lambda x: x.strftime('%Y-%m-%d--%a'))

        pivoted_df.columns = pd.MultiIndex.from_tuples([("Часы в Tempo", col) for col in pivoted_df.columns])
        pivoted_task_df.columns = pd.MultiIndex.from_tuples([("Задачи в Tempo", col) for col in pivoted_task_df.columns])

        # Переименовываем столбцы для лучшего восприятия
        pivoted_df.columns.name = ''
        pivoted_df.index.name = ''
        pivoted_task_df.columns.name = ''
        pivoted_task_df.index.name = ''

        # Заполнять пропущенные значения нолями
        pivoted_df.fillna(0, inplace=True)
        pivoted_task_df.fillna(0, inplace=True)

        # Отображаем итоговый DataFrame
        print(pivoted_df)
        print(pivoted_task_df)

        return pivoted_df, pivoted_task_df
    


class ReportJira():
    def __init__(self,
                 author_list,
                 jira_token,
                 project_key,
                 repo_slug,
                 month):
        
        self.author_list = author_list 
        self.jira_token = jira_token
        self.project_key = project_key
        self.repo_slug = repo_slug
        self.month = month


    # Находим id спринтов из 340 доски - "Load test"
    def sprints_id(self):
        sptint_url = f'https://{JIRA_URL}/rest/greenhopper/1.0/sprintquery/340?includeHistoricalSprints=true&includeFutureSprints=true'
        headers = {
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
                    'authority': f'https://{JIRA_URL}',
                    'Authorization': self.jira_token,
                    'accept': 'application/json, text/plain, */*'
                }

        response = requests.get(sptint_url, headers=headers)


        if response.status_code != 200:
            raise Exception(f"Ошибка запроса: {response.text}")

        #print(response.json())

        sprints_ids = [id['id'] for id in response.json()['sprints']]
        #for i in response.json()['sprints']:
        #    print(f"Name: {i['name']} - id: {i['id']}")
        print(sprints_ids)

        all_sprints_details = []
        for sprint_id in sprints_ids:
            details_url = f'https://{JIRA_URL}/rest/agile/latest/sprint/{sprint_id}'
            sprint_details = requests.get(details_url, headers=headers)
            #print(sprint_details.json())
            all_sprints_details.append(sprint_details.json())

        target_year = int(self.month.split('-')[0])
        target_month = int(self.month.split('-')[1])

        filtered_sprints = [
            sprint for sprint in all_sprints_details
            if (
                'startDate' in sprint and
                datetime.fromisoformat(sprint['startDate']).year == target_year and
                datetime.fromisoformat(sprint['startDate']).month == target_month
            ) 
                or
            (
                'completeDate' in sprint and
                datetime.fromisoformat(sprint['completeDate']).year == target_year and
                datetime.fromisoformat(sprint['completeDate']).month == target_month
            )
        ]

        if filtered_sprints:
            filtered_sprints_id = []
            print("Найдены спринты за июль 2025:")
            for sprint in filtered_sprints:
                print(f"Sprint ID: {sprint['id']} | Name: {sprint['name']} | Start Date: {sprint['startDate']}")
                filtered_sprints_id.append(sprint['id'])
            print(filtered_sprints_id)
            return filtered_sprints_id
        else:
            print("Нет спринтов за указанный период.")
            return None
        

    def issues_comments(self):
        stats = {}
        final_data = {}
        issue_url = f'https://{JIRA_URL}/rest/api/latest/search'
        headers = {
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
                    'authority': f'https://{JIRA_URL}',
                    'Authorization': self.jira_token,
                    'accept': 'application/json, text/plain, */*'
                }
        
        def sprints_dates(sprint_id):
            params = {
                'jql': f'sprint={sprint_id}',
                'maxResults': 500              
            }
            
            response = requests.get(issue_url, headers=headers, params=params)

            if response.status_code != 200:
                raise Exception(f"Ошибка запроса: {response.text}")
            
            #print(response.json()) #debug
            return response.json()
        
        data = [sprints_dates(sprint) for sprint in self.sprints_id()]

        for sprint in data:
            for issue in sprint['issues']: 
                print(f"Key: {issue['key']}, Summary: {issue['fields']['summary']}") # debug

                comments_url = f"https://{JIRA_URL}/rest/api/latest/issue/{issue['key']}/comment"
                comment_response = requests.get(comments_url, headers=headers)
                
                if comment_response.status_code == 200:
                    comments_data = comment_response.json()
                    
                    for comment in comments_data['comments']:
                        author_name = comment['author']['displayName']
                        created_at = comment['created']

                        date_obj = datetime.fromisoformat(created_at.split('T')[0])
                        formatted_date = date_obj.strftime('%Y-%m-%d')

                        if author_name not in stats:
                            stats[author_name] = {}
                        if formatted_date not in stats[author_name]:
                            stats[author_name][formatted_date] = 0
                        stats[author_name][formatted_date] += 1
                        
                        #print(f"\\tComment by {comment['author']['displayName']}: {comment['body']}\\n") #debug
                else:
                    print(f"Ошибка при получении комментариев: {comment_response.status_code}, {comment_response.text}")
        
        print(stats)

        for author, dates in stats.items():
            if author in self.author_list.values():
                for date, count in dates.items():
                    if date not in final_data:
                        final_data[date] = {}
                    final_data[date][author] = count
        

        df = pd.DataFrame(final_data).T.fillna(0).astype(int)

        first_day = datetime.strptime(self.month, '%Y-%m').replace(day=1)
        last_day = (first_day + pd.offsets.MonthEnd()).normalize()
        full_dates = pd.date_range(start=first_day, end=last_day)

        df.index = pd.to_datetime(df.index)
        df = df.reindex(full_dates, fill_value=0)
        df.index = df.index.strftime('%Y-%m-%d--%a')
        df = df.sort_index()

        df.columns = pd.MultiIndex.from_product([["Комментарии в Jira"], df.columns])

        df_filtered = df[~df.index.str.endswith(('--Sat', '--Sun'))] # Убираем выходные

        print(df_filtered)

        html = df_filtered.to_html()
        with open('jira.html', 'w') as w:
            w.write(html)

        return df_filtered
    


class ReportGit(ReportJira, ReportTempo):
    def __init__(self,
                 jira_user_list,
                 author_list,
                 conf_token,
                 jira_token,
                 project_key,
                 repo_slug,
                 month,
                 username,
                 passwd,
                 branches_limit=100,
                 commits_limit=10000):
        ReportJira.__init__(self,
                            author_list=author_list,
                            jira_token=jira_token,
                            project_key=project_key,
                            repo_slug=repo_slug,
                            month=month)
        ReportTempo.__init__(self,
                             author_list=author_list,
                             jira_user_list=jira_user_list,
                             jira_token=jira_token,
                             month=month)
        
        self.author_list = author_list 
        self.conf_token = conf_token
        self.project_key = project_key
        self.repo_slug = repo_slug
        self.month = month
        self.username = username
        self.passwd = passwd
        self.branches_limit = branches_limit
        self.commits_limit = commits_limit
        self.page_space = 'DEVQA'
        self.parent_page_title = 'STRESS_report ⬝ Monthly report desk'

    def get_branches(self):
        response = requests.get(
            f'https://{GIT_URL}/rest/api/latest/projects/{self.project_key}/repos/{self.repo_slug}/branches',
            params={'limit': self.branches_limit},
            auth=(self.username, self.passwd)
        )

        if response.status_code != 200:
            raise Exception("Ошибка запроса BRANCHES:", response.text)

        branches_data = response.json()
        branches = [branches['displayId'] for branches in branches_data['values']]
        print(branches)
        return branches


    def get_commits(self):
        branches_name = self.get_branches()

        def fetch_commits(branch_name):
            """
            Функция получает список коммитов для указанной ветки
            """
            response = requests.get(
                f'https://{GIT_URL}/rest/api/latest/projects/{self.project_key}/repos/{self.repo_slug}/commits',
                params={'until': branch_name, 'limit': self.commits_limit},  
                auth=(self.username, self.passwd)
            )

            if response.status_code != 200:
                raise Exception(f"Ошибка запроса COMMITS ({branch_name}): {response.text}")
            
            return response.json()['values'] 

        # Собираем коммиты по каждой ветке
        all_commits_by_branch = []
        for branch in branches_name:
            commits = fetch_commits(branch)
            all_commits_by_branch.append(commits)

        stats = {}

        for i, branch_commits in enumerate(all_commits_by_branch):
            branch_name = branches_name[i]
            #print(branch_name)
            for commit in branch_commits:
                author_name = commit['author']['name']
                timestamp_ms = commit['authorTimestamp'] // 1000  # Переводим миллисекунды в секунды
                formatted_date = pd.Timestamp(timestamp_ms, unit='s').strftime('%Y-%m-%d')
                
                if author_name not in stats:
                    stats[author_name] = {}
                if formatted_date not in stats[author_name]:
                    stats[author_name][formatted_date] = 0
                stats[author_name][formatted_date] += 1

        final_data = {}
        for author, dates in stats.items():
            if author in self.author_list.keys():
                for date, count in dates.items():
                    if date not in final_data:
                        final_data[date] = {}
                    final_data[date][author] = count
                
        df = pd.DataFrame(final_data).T.fillna(0).astype(int)

        first_day = datetime.strptime(self.month, '%Y-%m').replace(day=1)
        last_day = (first_day + pd.offsets.MonthEnd()).normalize()
        full_dates = pd.date_range(start=first_day, end=last_day)

        df.index = pd.to_datetime(df.index)
        df = df.reindex(full_dates, fill_value=0)
        df.index = df.index.strftime('%Y-%m-%d--%a')
        df = df.sort_index()

        df.columns = pd.MultiIndex.from_product([["Коммиты в Bitbucket"], df.columns])
        #df.columns.name = 'Коммиты в Bitbucket'

        month_mask = df.index.str.contains(self.month)
        filtered_df = df[month_mask]
        df_filtered = df[~df.index.str.endswith(('--Sat', '--Sun'))] # Убираем выходные
        #print(filtered_df)
        print(df_filtered)
        #print(df)

        df_jira = self.issues_comments()
        df_time_tempo, df_task_tempo = self.tempo_parse()

        df_filtered.rename(columns=self.author_list, inplace=True)

        result = pd.concat([df_filtered, df_jira, df_time_tempo, df_task_tempo], axis=1)
        result = result.sort_index(axis=1)

        #print(result)

        rebuild_result = result.to_dict()
        result = pd.DataFrame.from_dict(rebuild_result, orient="columns").fillna(0)
        result.columns = result.columns.swaplevel(0, 1)  # Меняем уровни местами

        expected_metrics_order = ['Комментарии в Jira', 'Коммиты в Bitbucket', 'Часы в Tempo', 'Задачи в Tempo']
        workers = sorted(result.columns.levels[0].unique())  # Список уникальных работников

        # Создаем порядок колонок
        new_column_order = [(worker, metric) for worker in workers for metric in expected_metrics_order]
        result = result.reindex(columns=new_column_order)

        # Сортируем столбцы по именам пользователей
        #result.sort_index(axis=1, level=0, sort_remaining=True, inplace=True)

        #print(res)

        # styles = [
        # {"selector": "th", "props": [("text-align", "center")]},
        # {"selector": "td", "props": [("text-align", "center"), ("max-width", "150px"), ("overflow", "hidden"), ("text-overflow", "ellipsis")]}]

        # styled_df = result.style.set_table_styles(styles)
        # html_output = styled_df.to_html(classes=["table", "table-bordered"], escape=False)

        css_style = '''
        <style>
        .center th, .center td { text-align: center; }
        </style>
        '''

        pre_html = result.to_html(classes=['center'])
        html = css_style + pre_html
        with open('git.html', 'w') as w:
            w.write(html)



class MonthlyReport(ReportGit):
    def __init__(self,
                 jira_user_list,
                 author_list,
                 conf_token,
                 jira_token,
                 project_key,
                 repo_slug,
                 month,
                 username,
                 passwd):
        
        super().__init__(jira_user_list=jira_user_list,
                         author_list=author_list,
                         conf_token=conf_token,
                         jira_token=jira_token,
                         project_key=project_key,
                         repo_slug=repo_slug,
                         month=month,
                         username=username,
                         passwd=passwd)
        
    
    def write_html(self, string):
        with open('result.html', 'a') as w:
            w.write(string)
        
    def get_dates(self):
        self.get_commits()
        

        self.write_html('<h1>Активность сотрудников отдела Нагрузочного тестирования</h1>')
        self.write_html('<p>Настоящий отчет представляет собой систематизированную сводку ежедневной активности сотрудников, включающую следующие показатели:</p>')
        self.write_html('<ul><li>Количество комментариев к задачам в системе управления проектами <strong>Jira</strong>;</li>')
        self.write_html('<li>Число коммитов каждого сотрудника в репозитории системы контроля версий <strong>Bitbucket</strong>.</li>')
        self.write_html('<li>Суммарное количество отработанного времени за день по всем задачам <strong>Tempo</strong>.</li>')
        self.write_html('<li>Задачи, на которые списано время за день <strong>Tempo</strong>.</li></ul>')
        self.write_html('<p>Отсутствие зафиксированной активности сотрудника по всем показателям (Jira и Bitbucket) в течение дня обозначается выделением всех соответствующих ячеек фоновым оттенком '
        '<span style="background-color: #ffffe1; color: #fe5555;">желтого цвета</span>.</p>')



    

    def soup_background(self, html):
        soup = BeautifulSoup(html, 'html.parser')

        # Размер блока (каждый автор занимает столько колонок)
        block_size = 4
        len_size = len(self.author_list.values())

        # Список авторов (ключами служат позиции колонок)
        sorted_authors = sorted(self.author_list.values())
        AUTHOR_LIST = {name: index * block_size for index, name in enumerate(sorted_authors)}


        # Проходим по строкам
        for row in soup.select("tbody tr"):
            tds = row.find_all("td")  # Все ячейки строки
            
            # Для каждого автора проверяем ячейки
            for author, pos in AUTHOR_LIST.items():
                jira_col_idx = pos
                bitbucket_col_idx = pos + block_size // block_size
                
                # Проверяем значения ячеек
                jira_value = tds[jira_col_idx].text.strip()
                bitbucket_value = tds[bitbucket_col_idx].text.strip()
                
                # Если оба значения равны 0, выделяем
                if jira_value == '0' and bitbucket_value == '0':
                    tds[jira_col_idx]['style'] = 'background-color: #ffffe1; color: #fe5555;'
                    tds[bitbucket_col_idx]['style'] = 'background-color: #ffffe1; color: #fe5555;' 

        updated_html = soup.prettify()
        with open('git.html', 'w') as w:
            w.write(updated_html)
        

    def soup_macro(self, html):
        soup = BeautifulSoup(html, 'html.parser')

        # Шаблон макроса Jira
        jira_macro_template = """<ac:structured-macro ac:name="jira" ac:schema-version="1" ac:macro-id="e586f42e-cab2-426c-97d5-692afd3dee26">
            <ac:parameter ac:name="server">Jira - Astra Linux</ac:parameter>
            <ac:parameter ac:name="serverId">d19f6132-65dc-37bb-94ca-4be05d9bb688</ac:parameter>
            <ac:parameter ac:name="key">{task_id}</ac:parameter>
            <ac:parameter ac:name="columns">key,summary,type,created,updated,due,assignee,reporter,priority,status,resolution</ac:parameter>
        </ac:structured-macro>"""

        
        for cell in soup.find_all('td'):
            # Проверяем наличие текста в ячейке
            if cell.string is not None:
                text_content = cell.string.strip()
                # Разделяем строки, содержащие несколько номеров задач
                task_ids = text_content.split(',')
            
                new_contents = []
                for task_id in task_ids:
                    stripped_task_id = task_id.strip()  # Удаление пробелов вокруг
                    if stripped_task_id.startswith(('DEV', 'LIF', 'SIR', 'BR', 'BT')):
                        # Создаем новый элемент с макросом
                        macro_tag = BeautifulSoup(jira_macro_template.format(task_id=stripped_task_id), 'html.parser').contents[0]
                        new_contents.append(macro_tag)
                    else:
                        # Оставляем оригинальный текст, если он не начинается с 'DEVQA-'
                        new_contents.append(stripped_task_id)
            
                # Собираем новые элементы обратно в ячейку
                cell.clear()
                for content in new_contents:
                    cell.append(content)

        return str(soup)

    
    def post_report(self):
        
        #Создаем новую html страницу
        with open('git.html', 'r') as r:
            #html_table = r.readlines()
            html_table = r.read()

        self.soup_background(html_table)

        with open('git.html', 'r') as r:
            html_table = r.read()

        self.write_html(self.soup_macro(html_table))


        with open('result.html', 'r') as r:
            table = r.read()

        confluence_report = ReportToConfluence(username=self.username, password=None, token=self.conf_token)
        confluence_report.create_confluence_page(self.page_space, self.parent_page_title, self.month)
        confluence_report.update_confluence_page(self.page_space, self.month, table)



        #if path.isfile('res.html'):
            #remove('res.html')
        if path.isfile('result.html'):
            remove('result.html')