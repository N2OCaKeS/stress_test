import requests

jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text

SCRIPT_DIR = '/home/u/git/stress_test/parsec'
REPORT_FILENAME = 'test_results.json'
FLAMEGRAPH_NAME = 'result_flamegraph.svg'
REPORT_PATH = f'{SCRIPT_DIR}/report'
TEMPLATE_PATH = f'{SCRIPT_DIR}/templates'
TIMEDF_NAME = f'{TEMPLATE_PATH}/timedf.html'
TOTALDF_NAME = f'{TEMPLATE_PATH}/totaldf.html'
DETAILDF_NAME = f'{TEMPLATE_PATH}/detaildf.html'
INFO_FILENAME = 'ps_info.txt'
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'

#количество создаваемых потоков 
CONC = 35
#количество циклов для каждого потока
COUNTER = 100000

FILE_SYSTEM = 'tmpfs'



parsec_function_old = ['parsec_capable_ilev', 'parsec_inode_permission_a', 'i_pdpl_get',
        'parsec_secid', 'pdpl_put', 'pdpl_get', 'parsec_current_permission', 'parsec_caps_task_get',
        'caps_cpy', 'pdpml_permission', 'parsec_realpath_from_dentry', 'parsec_task_lbl_get',
        'parsec_log', 'parsec_inode_permission', 'parsec_audit_rule_match', 'pdpl_permission',
        'psc_audit_check', 'parsec_confine_check', 'is_kernel_audit', 'i_audit_get', 'di_audit_init',
        'pdpml_conf_permission', 'di_pdpl_set_from_tsk', 'di_pdpl_set', 'parsec_file_open',
        'di_faud_from_xattr', 'pdpl_cmp', 'irelax_permission', 'di_pdpl_init', 'parsec_path_mknod',
        'psc_secid_to_lbl', 'inode_post_create', 'parsec_instantiate', 'parsec_i_alloc_security',
        'di_getxattr', 'file_audit_inherit_default_audit', 'parsec_i_free_security', 'parsec_inode_init_xattrs',
        'di_pdpl_from_xattr', 'parsec_path_hook.isra.0', 'pdpl_dup', 'pdpl_inherit_integrity', 'cell_check',
        'pdpml_put', 'do_i_init_security', 'aud_cpy', 'di_chlbl_permission', 'do_post_create',
        'parsec_current_getsecid_subj', 'pdpl_or_type', 'faud_dup', 'parsec_oracle_check_dentry',
        'inode_use_init_xattrs.isra.0', 'I_LBL_UPD', 'parsec_aud_task_get', 'pdpl_inherit_secrecy']

