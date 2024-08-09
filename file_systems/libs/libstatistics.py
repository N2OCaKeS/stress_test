import requests
import json


class FileSystemStatistics: 
    def __init__(self,
                 username,
                 token):
        
        self.username = username
        self.token = token
        self.url = 'http://allta.devos.astralinux.ru:7777/base-statistics'

    def update_statistics(self):
        data = {
            'title_statistics':'Файловые системы',
            'username':self.username,
            'token':self.token,
            'set_of_test_types':['EXFAT', 'EXT2', 'EXT4', 'EXT4_parsec', 'FAT', 'NTFS', 'XFS', 'XFS_parsec', 'OCFS2'],
            'comparison_list':[['EXT4', 'XFS'], ['EXT4', 'EXT4_parsec']]
        }

        headers = {
            'Content-Type': 'application/json'
            }
        
        requests.post(url=self.url, data=json.dumps(data), headers=headers)
        