import pandas as pd


def parsing_table_with_results(soup):

    table = soup.find_all('table')[1]
    headers = [th.get_text(strip=True) for th in table.find_all('th')[:4]]

    data = []
    for row in table.find_all('tr')[1:]:
        cols = row.find_all('td')[:4]     
        row_data = [col.get_text(strip=True) for col in cols]
        
        if all(row_data):
            data.append(row_data)

    df = pd.DataFrame(data, columns=headers)

    # Преобразуем столбцы в числа, заменяя ошибки на NaN
    df['user_count'] = pd.to_numeric(df['user_count'], errors='coerce').astype('Int64')
    for col in headers[1:]:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.dropna()

    return df