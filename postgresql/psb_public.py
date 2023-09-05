import json
from libs.zefir import UploaderZC

with open('psb_public_args.json', 'r') as r:
    public_args = json.load(r)

uzs = UploaderZC(folder_tree_id=public_args['folder_tree_id'],
                 test_cycle_name=public_args['test_cycle_name'],
                 test_case_name=public_args['test_case_name'],
                 basic_auth=public_args['basic_auth'],
                 test_cycle_version=public_args['test_cycle_version'],
                 token=public_args['token'],
                 username=public_args['username'],
                 conf_space=public_args['conf_space'],
                 conf_parent_page=public_args['conf_parent_page'],
                 conf_new_page_name=public_args['conf_new_page_name'],
                 grade_stand=public_args['grade_stand'],
                 package=public_args['package'],
                 public=True,
                 statistics=True)

uzs.upload_test_cycle_status(zefir_status='pass')

