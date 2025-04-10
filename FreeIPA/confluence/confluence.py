from atlassian import Confluence

from confluence.confluence_conf import CONFLUENCE_URL

class ConfluencePage:
    __url = f'https://{CONFLUENCE_URL}'

    def __init__(self, username, password=None, token=None):
        self.__username = username
        self.__password = password
        self.__access_token = token

        if self.__password is not None:
            self.__confluence = Confluence(url=self.__url,
                                           username=self.__username,
                                           password=self.__password)
        elif self.__access_token is not None:
            self.__confluence = Confluence(url=self.__url,
                                           username=self.__username,
                                           token=self.__access_token)

    def get_page_as_html(self,
                         page_space=None,
                         page_title=None, id=None):

        if id:
            html_page = self.__confluence.get_page_by_id(id, expand="body.view")
            return html_page
        if self.__confluence.page_exists(space=page_space, title=page_title):
            html_page = self.__confluence.get_page_by_title(space=page_space, title=page_title, expand="body.view")
            return html_page
        else:
            return None
        
    def get_child_page_as_html(self, id=None, by_title=True):
        if id:
            if by_title:
                html_page = self.__confluence.get_child_title_list(id)
            else:
                html_page = self.__confluence.get_child_id_list(id)
            return html_page
        else:
            return None

class UpdatePageReportToConfluence():
    __url=f'https://{CONFLUENCE_URL}'
    
    def __init__(self, username, password=None, token=None):
        self.__username = username
        self.__password = password
        self.__access_token = token

        if self.__password is not None:
            self.__confluence = Confluence(url=self.__url,
                                           username=self.__username,
                                           password=self.__password)
        elif self.__access_token is not None:
            self.__confluence = Confluence(url=self.__url,
                                           username=self.__username,
                                           token=self.__access_token)
            

    def attache_files(self, file, page_space, page_title):
        self.__confluence.attach_file(filename=file,
                                      page_id=self.__confluence.get_page_id(space=page_space,
                                                                            title=page_title),
                                      title=page_title,
                                      space=page_space)
        
    def get_confluence_page_id(self, page_space, page_title):
        return self.__confluence.get_page_id(space=page_space, title=page_title)


    def get_confluence_public_url(self, page_space, page_title):
        return ('{url}/pages/viewpage.action?pageId={id}#'.format(url=self.__url,
                                                                  id=self.__confluence.get_page_id(space=page_space,
                                                                                                   title=page_title)))
    
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