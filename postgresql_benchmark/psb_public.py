import json
from libs.libpublic import Public
from libs.zefir import Zefir_status_API, Zefir_result_table


with open('psb_public_args.json', 'r') as r:
    public_args = json.load(r)

public = Public(username=public_args['username'],
                token=public_args['token'],
                conf_space=public_args['conf_space'],
                conf_parent_page=public_args['conf_parent_page'],
                conf_new_page_name=public_args['conf_new_page_name'],
                grade_stand=public_args['grade_stand'],
                package=public_args['package'])

public.run_publish()

zefir = Zefir_status_API(folder_tree_id=public_args['folder_tree_id'],
                         test_cycle_name=public_args['test_cycle_name'],
                         test_case_name=public_args['test_case_name'],
                         basic_auth=public_args['basic_auth'])
zefir.upload_status(91)

zefir_table = Zefir_result_table(test_cycle_version=public_args['test_cycle_version'],
                                 token=public_args['token'],
                                 basic_auth=public_args['basic_auth'],
                                 username=public_args['username'])
zefir_table
