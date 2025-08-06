import os
import json

from libreport import MonthlyReport
#from dotenv import load_dotenv


# Указать отчетный период "месяц" в формате "YYYY-MM"
MONTH = '2025-08'


with open('/home/u/tokens.json', 'r') as r:
    tokens = json.load(r)
CONF_TOKEN = tokens['conf_token']
USERNAME = tokens['username']
JIRA_TOKEN = tokens['jira_token']
PASSWORD = ''

PROJECT_KEY = 'QA'
REPO_SLUG = 'stress_test'

# Список сотрудников
AUTHOR_WHITE_LIST = { 
    'ivelikanov': 'Иван Великанов',
    'mfilippenko': 'Максим Филиппенко', 
    'dtimonin': 'Дмитрий Тимонин'
    }
AUTHOR_JIRA_LIST = { 
    'JIRAUSER46491': 'Иван Великанов',
    'JIRAUSER96504': 'Максим Филиппенко', 
    'JIRAUSER38882': 'Дмитрий Тимонин'
    }



report = MonthlyReport(author_list=AUTHOR_WHITE_LIST,
                       jira_user_list=AUTHOR_JIRA_LIST,
                       conf_token=CONF_TOKEN,
                       jira_token=JIRA_TOKEN,
                       project_key=PROJECT_KEY,
                       repo_slug=REPO_SLUG,
                       month=MONTH,
                       username=USERNAME,
                       passwd=PASSWORD)


report.get_dates()
report.post_report()

