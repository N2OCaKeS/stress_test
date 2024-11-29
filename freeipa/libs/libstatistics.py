import requests
import json


class FreeipaStatistics: 
    def __init__(self,
                 username,
                 token):
        
        self.username = username
        self.token = token
        self.url = 'http://allta.devos.astralinux.ru:7777/freeipa-statistics'

    def update_statistics(self):
        data = {
            'title_statistics':'FreeIPA',
            'username':self.username,
            'token':self.token,
            'set_of_test_types':['FreeIPA auth']
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