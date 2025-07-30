import os

from libreport import MonthlyReport
from dotenv import load_dotenv

# TODO 
#Использовать dotenv для кредов

AUTHOR_WHITE_LIST = {
    'amedvedev': 'Александр Медведев', 
    'ivelikanov': 'Иван Великанов',
    'mfilippenko': 'Максим Филиппенко', 
    'dtimonin': 'Дмитрий Тимонин'
    }
AUTHOR_JIRA_LIST = {
    'JIRAUSER95917': 'Александр Медведев', 
    'JIRAUSER46491': 'Иван Великанов',
    'JIRAUSER96504': 'Максим Филиппенко', 
    'JIRAUSER38882': 'Дмитрий Тимонин'
    }

PROJECT_KEY = 'QA'
REPO_SLUG = 'stress_test'
USERNAME = ''
PASSWORD = ''
JIRA_TOKEN = ''
CONF_TOKEN = ''
MONTH = '2025-07'


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

