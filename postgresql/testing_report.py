import json
import pandas as pd


with open('report.json', 'r') as file:
    report_data = json.load(file)

sql_requests = report_data['result'].keys()

for sql_request in sql_requests:
    print(f"SQL запрос: {sql_request}")
    rows = []
    for thread_count in report_data['result'][sql_request].keys():
        row = {
                "threads": thread_count, 
                "p99": report_data['result'][sql_request][thread_count]['p99'], 
                "p95": report_data['result'][sql_request][thread_count]['p95'], 
                "p50": report_data['result'][sql_request][thread_count]['p50'], 
                "min": report_data['result'][sql_request][thread_count]['min'], 
                "max": report_data['result'][sql_request][thread_count]['max']
            }
        
        rows.append(row)
    df = pd.DataFrame(rows)
    print(df)
        
        