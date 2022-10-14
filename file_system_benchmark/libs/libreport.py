# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

from shutil import unpack_archive
from atlassian import Confluence


class ReportToConfluence():
    __url='https://life.astralinux.ru'

    def __init__(self, username, password):
        self.__username = username
        self.__password = password

        self.__confluence = Confluence(url=self.__url,
                                       username=self.__username,
                                       password=self.__password)
        self.report_files_path = '.'

    def unzip_tarfile(self, tar_name, extract_path='.'):
        unpack_archive(tar_name, extract_path)
        self.report_files_path = extract_path+'/report'

    def create_page_body(self):
        pass

    def attache_files(self, file, page_space, page_title):
        self.__confluence.attach_file(filename=file,
                                      page_id=self.__confluence.get_page_id(space=page_space,
                                                                            title=page_title),
                                      title=page_title,
                                      space=page_space)

    def get_confluence_page_id(self, page_space, page_title):
        return self.__confluence.get_page_id(space=page_space, title=page_title)

    def create_confluence_page(self,
                               page_space,
                               parent_page_title,
                               page_title,
                               page_body='this page was automatically created',):
        if not self.__confluence.page_exists(space=page_space, title=page_title):
            if self.__confluence.create_page(space=page_space,
                                             title=page_title,
                                             body=page_body,
                                             parent_id=self.__confluence.get_page_id(space=page_space,
                                                                                     title=parent_page_title),
                                             type='page',
                                             representation='storage',
                                             editor='v2'):
                print('+++ page {} is ready in space {}'.format(page_title, page_space))

    def update_confluence_page(self,
                               page_space,
                               page_title,
                               page_body,):
        if self.__confluence.page_exists(space=page_space, title=page_title):
            self.__confluence.update_page(page_id=self.__confluence.get_page_id(space=page_space, title=page_title),
                                          title=page_title,
                                          body=page_body)