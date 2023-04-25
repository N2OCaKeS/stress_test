import json
from libs.libpublic import Public


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