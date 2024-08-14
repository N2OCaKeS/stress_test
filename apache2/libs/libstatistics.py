import requests
import json


class ApacheStatistics: 
    def __init__(self,
                 username,
                 token):
        
        self.username = username
        self.token = token
        self.url = 'http://allta.devos.astralinux.ru:7777/base-statistics'

    def update_statistics(self):
        data = {
            'title_statistics':'Apache',
            'username':self.username,
            'token':self.token,
            'set_of_test_types':['apache-rp']
        }

        headers = {
            'Content-Type': 'application/json'
            }
        
        requests.post(url=self.url, data=json.dumps(data), headers=headers)