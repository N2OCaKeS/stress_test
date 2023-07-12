import json
from libs.libpublic import Public
from libs.zefir import ZefirStatusAPI, ZefirResultTable
from libs.libstatistics import PSQLStatistics2
from time import ctime, sleep
from libs.libpsb import response
from os import path

with open('psb_public_args.json', 'r') as r:
    public_args = json.load(r)

def upload_result_status():
    public = Public(username=public_args['username'],
                    token=public_args['token'],
                    conf_space=public_args['conf_space'],
                    conf_parent_page=public_args['conf_parent_page'],
                    conf_new_page_name=public_args['conf_new_page_name'],
                    grade_stand=public_args['grade_stand'],
                    package=public_args['package'])

    public.run_publish()

    zefir = ZefirStatusAPI(folder_tree_id=public_args['folder_tree_id'],
                            test_cycle_name=public_args['test_cycle_name'],
                            test_case_name=public_args['test_case_name'],
                            basic_auth=public_args['basic_auth'])
    zefir.upload_status(91)

    zefir_table = ZefirResultTable(test_cycle_version=public_args['test_cycle_version'],
                                    token=public_args['token'],
                                    basic_auth=public_args['basic_auth'],
                                    username=public_args['username'])
    zefir_table

    statistics = PSQLStatistics2(username=public_args['username'], 
                                token=public_args['token'])
    statistics.update_statistics()

end_status = 0
while end_status == 0:
    jira_end, life_end = response()
    try:
        if jira_end == 200 and life_end == 200:
            upload_result_status()
            end_status += 1
        else: 
            with open('JIRA_ERROR.log', 'a') as err:
                err.write('end:\n')
                err.write(ctime())
                err.write(f'jira_status = {jira_end}\nlife_status = {life_end}')
                err.write('---------' * 25)
                err.write('\n\n')
            sleep(60)
    except Exception as e:
        with open('JIRA_ERROR.log', 'a') as err:
            err.write('end:\n')
            err.write(ctime())
            err.write(str(e))
            err.write('---------' * 25)
            err.write('\n\n')
            end_status += 1

if path.isfile('libs/zefir.log'):
    with open('libs/zefir.log', 'r') as r:
        zefir_log = r.read()
        print('\n\n\nZefir-log\n')
        print(zefir_log)
if path.isfile('JIRA_ERROR.log'):
    with open('JIRA_ERROR.log', 'r') as r:
        jira_log = r.read()
        print('\n\n\nJira-log\n')
        print(jira_log)
