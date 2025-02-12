from string import Template


changing_files = {
     'install.sh': ['PROJECT_PATH', 'DB_PATH']
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



with open('infocollector.conf', 'r') as r:
    config = r.readlines()
#print(config)

dates = {
    line.split('=')[0]: line.split('=')[1].strip() for line in config if '=' in line
}
#print(dates)


[var_wrapper(files, dates) for files in changing_files.keys()]


