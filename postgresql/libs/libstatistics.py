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
            'set_of_test_types':['postgresql', 'postgresql-sm', 'postgresql-aud-off', 'psql parsec', 'psql vanilla', 'tantor vanilla', 'psql balance'],
            'comparison_list':[
                ['postgresql', 'postgresql-sm'], 
                ['postgresql', 'postgresql-aud-off'], 
                ['postgresql', 'psql parsec'], 
                ['postgresql', 'psql vanilla']
            ],
            'comparison_kernel_list':['postgresql']
        }

        headers = {
            'Content-Type': 'application/json'
            }
        
        res = requests.post(url=self.url, data=json.dumps(data), headers=headers)
        print("\n############\nSTATISTICS LOGS START\n")
        if res.status_code == 200:
            response = res.json()
            
            print(f"STATUS: {response.get('status')}")
            print(f"MESSAGE\n{''.join(response.get('message'))}")
           
        else:
            print("НЕизвестная ошибка, даже request на URL не сделался")
        print("\n############\nSTATISTICS LOGS END\n")
        


if __name__ == "__main__":
    stat = PSQLStatistics(username="ivelikanov", token="MDU1MTE3OTUwODIxOmr0OiMQFYNxnZrMgIz16KVcyX9j")
    stat.update_statistics()
