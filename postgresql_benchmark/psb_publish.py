from libs.libreport import ReportToConfluence
from libs.libtable import Report

r = ReportToConfluence('rkuznetsov', 'AstraFro-man')
# r.unzip_tarfile('report1.7_orel_1665647784.6084247.tar')
# r.create_confluence_page('~rkuznetsov', 'Роман Кузнецов: личная страница.', 'test')
# for file in os.listdir(r.report_files_path):
#     r.attache_files('{}/{}'.format(r.report_files_path, file), '~rkuznetsov', 'test')


with open('templates/header_table_template.html', 'r') as file:
    header_table = file.read()
with open('templates/rating_template.html', 'r') as file:
    rating = file.read()
with open('templates/img_template.html', 'r') as file:
    img = file.read()
with open('report/psb_report_table.html', 'r') as file:
    main_table = file.read()

html_page = '\n'.join([header_table, rating, main_table, img])

r.update_confluence_page('~rkuznetsov', 'test', html_page)

# # Добавить тестовую таблицу
# with open('./report/psb_report_table.html', 'r') as file:
#     table = file.read()
#     r.update_confluence_page('~rkuznetsov', 'test', table)

# '<img class="confluence-embedded-image" draggable="false" src="/download/attachments/140673955/psb_clients_la_graph.png?version=1&amp;modificationDate=1665389769281&amp;api=v2" data-image-src="/download/attachments/140673955/psb_clients_la_graph.png?version=1&amp;modificationDate=1665389769281&amp;api=v2" data-unresolved-comment-count="0" data-linked-resource-id="140673989" data-linked-resource-version="1" data-linked-resource-type="attachment" data-linked-resource-default-alias="psb_clients_la_graph.png" data-base-url="https://life.astralinux.ru" data-linked-resource-content-type="image/png" data-linked-resource-container-id="140673955" data-linked-resource-container-version="6" height="400">'