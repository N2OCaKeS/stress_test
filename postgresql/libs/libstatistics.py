import requests



class PSQLStatistics: 
    def __init__(self,
                 username,
                 token):
        
        self.username = username
        self.token = token
        self.url = 'allta.devos.astralinux.ru:7777/base-statistcs'

    def update_statistics(self):
        data = {
            'title_statistics':'PostgreSQL',
            'username':self.username,
            'token':self.token,
            'set_of_test_types':["postgresql", "postgresql-sm", "postgresql-aud-off", "psql_parsec", "psql_vanilla", "tantor_vanilla"],
            'comparison_list':[
                ["postgresql", "postgresql-sm"], 
                ["postgresql", "postgresql-aud-off"], 
                ["postgresql", "psql_parsec"], 
                ["postgresql", "psql_vanilla"]
            ],
            'comparison_kernel_list':["postgresql"]
        }

        requests.post(url=self.url, data=data)

