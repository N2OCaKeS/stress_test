import requests
import json


class ParsecStatistics: 
    def __init__(self,
                 username,
                 token):
        
        self.username = username
        self.token = token
        self.url = 'http://allta.devos.astralinux.ru:7777/parsec-statistics'

    def update_statistics(self):
        data = {
            'title_statistics':'Parsec',
            'username':self.username,
            'token':self.token,
            'set_of_test_types':['parsec_impact-fs', 'parsec_impact-fs-aud-off'],
        }

        headers = {
            'Content-Type': 'application/json'
            }
        
        requests.post(url=self.url, data=json.dumps(data), headers=headers)