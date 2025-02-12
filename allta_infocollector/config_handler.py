from string import Template
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
changing_files = {
     f'{script_dir}/install.sh': ['PROJECT_PATH', 'DB_PATH'],
     f'{script_dir}/src/handler/prometheus.yml': ['SERVERS'],
     f'{script_dir}/src/handler/prepare_handler.sh': ['PM_DB_PATH', 'DB_PATH'],
     f'{script_dir}/src/handler/import_dashboard_full.sh': ['DB_PATH', 'SERVER_IP'],
     f'{script_dir}/src/handler/import_dashboard_allta.sh': ['DB_PATH', 'SERVER_IP'],
     f'{script_dir}/src/handler/docker-compose.yml': ['PM_DB_PATH'],
     f'{script_dir}/src/aggregator/conf.py': ['STD_USER', 'STD_PASSWD', 'PROJECT_PATH', 'SERVER_IP']
}


def var_wrapper(file_name: str, dates: dict):
    temp_dict = {
        value: dates[value] for value in changing_files[file_name] if value in dates.keys()
        }
    print(temp_dict)

    with open(file_name, 'r') as vars_file:
            vars_template = Template(vars_file.read())
            new_vars = vars_template.safe_substitute(**temp_dict)
            print(new_vars)

    with open(file_name, 'w') as n_vars:
         n_vars.write(new_vars)



with open(f'{script_dir}/infocollector.conf', 'r') as r:
    config = r.readlines()
#print(config)

dates = {
    line.split('=')[0]: line.split('=')[1].strip() for line in config if '=' in line
}
#print(dates)


[var_wrapper(files, dates) for files in changing_files.keys()]


