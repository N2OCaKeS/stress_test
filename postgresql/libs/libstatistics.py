import requests
import json


class PSQLStatistics: 
    def __init__(self,
                 username,
                 token):
        
        self.username = username
        self.token = token
        self.url = 'http://allta.devos.astralinux.ru:7777/postgresql-statistics'

    def update_statistics(self):
        data = {
            'title_statistics':'PostgreSQL',
            'username':self.username,
            'token':self.token,
            'set_of_test_types':['postgresql', 'postgresql-sm', 'postgresql-aud-off', 'psql_parsec', 'psql_vanilla', 'tantor_vanilla', 'psql_balance'],
            'comparison_list':[
                ['postgresql', 'postgresql-sm'], 
                ['postgresql', 'postgresql-aud-off'], 
                ['postgresql', 'psql_parsec'], 
                ['postgresql', 'psql_vanilla']
            ],
            'comparison_kernel_list':['postgresql']
        }

        headers = {
            'Content-Type': 'application/json'
            }
        
        requests.post(url=self.url, data=json.dumps(data), headers=headers)

