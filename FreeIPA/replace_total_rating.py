import re
from bs4 import BeautifulSoup

def replace_total_rating_in_html(html_text, new_total_rating):
    if isinstance(html_text, BeautifulSoup):
        html_str = str(html_text)
        new_html = re.sub(r'Total rating: \d+\.\d+', f'Total rating: {new_total_rating}', html_str) 
        return new_html