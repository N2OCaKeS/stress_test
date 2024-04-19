import re
import pandas as pd

html_head = '<div style="border:1px solid black; padding:10px;">\n'
html_string = '<p><style="font-family: Century Gothic, sans-serif;">{}"</p>\n'
with open('test.txt', 'r') as r:
    text = r.readlines()

values = {
    'read_iops':[text[i].split(' ') for i in range(len(text)) if 'read' in text[i] and 'IOPS' in text[i]],
    'read_clat':[text[i+2].split(' ') for i in range(len(text)) if 'read' in text[i] and 'IOPS' in text[i]],
    'write_iops':[text[i].split(' ') for i in range(len(text)) if 'write' in text[i] and 'IOPS' in text[i]],
    'write_clat':[text[i+2].split(' ') for i in range(len(text)) if 'write' in text[i] and 'IOPS' in text[i]]
}

def k_parser(value:str):
    if value.endswith('k,'):
        return str(float(re.findall(r'\d+.\d+?', value)[0]) * 1000).replace('.0', '')
    else: return re.findall(r'\d+.\d+?', value)[0]


dates = {
    'write':{'IOPS':k_parser(values['write_iops'][0][3]),
             'Latency/avg':k_parser(values['write_clat'][0][8])},
    'read':{'IOPS':k_parser(values['read_iops'][0][3]),
             'Latency/avg':k_parser(values['read_clat'][0][8])}
}

print(dates)


df = pd.DataFrame(dates).T

print(df)

#print([html_string.format(i) for i in text])

# with open('test1.html', 'w') as w:
#     w.write(html_head)
# with open('test1.html', 'a') as w:
#     w.writelines(html_string.format(i.strip()) for i in text)
# with open('test1.html', 'a') as w:
#     w.write('</dev>')


