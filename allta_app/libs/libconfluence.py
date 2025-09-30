import requests

from atlassian import Confluence


confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_confluence_url = requests.get(confluence_url_api)
CONFLUENCE_URL = response_confluence_url.text




class ConfluenceAPI():
    __url=f"https://{CONFLUENCE_URL}"

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
            

    def get_confluence_blog_id(self, 
                               page_space, 
                               page_title,
                               type):
        return self.__confluence.get_page_id(space=page_space, 
                                             title=page_title, 
                                             type=type)
    
    def get_all_pages_from_blog(self, 
                                blog_space, 
                                start,
                                limit,
                                content_type):
        return self.__confluence.get_all_pages_from_space(space=blog_space, 
                                                          limit=limit,
                                                          start=start,
                                                          content_type=content_type)
    
    def add_comment(self,
                    blog_page_id,
                    text):
        return self.__confluence.add_comment(page_id=blog_page_id,
                                             text=text)
    
    def get_page_comments(self,
                          blog_page_id):
        return self.__confluence.get_page_comments(content_id=blog_page_id,
                                                   expand="body.storage")
    
    def get_child_pages(self,
                        page_id):
        return self.__confluence.get_child_pages(page_id=page_id)
    
    def get_page_by_id(self,
                       page_id):
        return self.__confluence.get_page_by_id(page_id=page_id)
    
    def get_full_page_by_id(self,
                            page_id):
        return self.__confluence.get_page_by_id(page_id=page_id,
                                                expand="body.storage")
    




class SendCommentToConfluence(ConfluenceAPI):
    def __init__(self,
                 rc_name: str,
                 username: str,
                 token: str):
        super().__init__(username=username, 
                         token=token)

        self.rc = rc_name
        self.check_len_version = self.rc.split('.')

        if len(self.check_len_version) == 4 and self.check_len_version[3] != 'UU':
            self.release_version = '.'.join(self.check_len_version[:3])
            self.rc_version = self.check_len_version[-1]
            self.load_page_version = self.release_version
            self.rc_template = f"RC{self.rc_version} оперативного обновления Astra Linux SE {self.release_version}"
        elif len(self.check_len_version) == 6 and self.check_len_version[3] == 'UU':
            self.release_version = '.'.join(self.check_len_version[:4]) + self.check_len_version[4]
            self.rc_version = self.check_len_version[-1]
            self.load_page_version = '.'.join(self.check_len_version[:5])
            self.rc_template = f"RC{self.rc_version} срочного обновления Astra Linux SE {self.release_version}"

        print(f"release_version: {self.release_version}")
        print(f"rc_version: {self.rc_version}")
        print(f"load_page_version: {self.load_page_version}")


    def _get_load_page_statistics_url(self, load_page_version: str):
        ID_ROOT_PAGE_17 = 156339086
        ID_ROOT_PAGE_18 = 244154033
        prefix = "STRESS_report ⬝ "
        url_template = f"https://{CONFLUENCE_URL}"
        raw_link = "<a href=\"{}\">Добавлены результаты для \"{}\"</a><br><br>"
        
        if load_page_version.startswith("1.7"):
            id_root_page = ID_ROOT_PAGE_17
        elif load_page_version.startswith("1.8"):
            id_root_page = ID_ROOT_PAGE_18

        load_child_pages = self.get_child_pages(page_id=id_root_page)
        load_pages_dict = {
                page["title"]: page["_links"]["webui"] for page in load_child_pages
            }
        #print(load_pages_dict)

        try:
            link = load_pages_dict[prefix + load_page_version]
            url = url_template + link
            full_link = raw_link.format(url, self.rc_template)
            print(f"full_link: {full_link}")
            return str(full_link)
        except KeyError:
            print(f"Страница с результатами для {load_page_version} не найдена")
            return 'Fail'
            #return f"<span style=\"color:red;\">Страница с результатами для {load_page_version} не найдена</span><br><br>"


    def send_comment(self):
        start_point = 0
        if self._get_load_page_statistics_url(self.load_page_version) == 'Fail':
            return None
        statistic_url = self._get_load_page_statistics_url(self.load_page_version)
        comment_text = f"""
                        <strong>Нагрузочное тестирование</strong><br>
                        {statistic_url}
                        <em>this comment was automatically created</em>
                        """
        def __pages_dict(start):
            all_pages = self.get_all_pages_from_blog(blog_space="AL",
                                                    start=start,
                                                    limit=10000,
                                                    content_type="blogpost")

            pages_dict = {
                page["title"]: page["id"] for page in all_pages
            }

            return pages_dict

        pages_dict = __pages_dict(start_point)
        pages_len = len(__pages_dict(start_point))
        print(f"start point: {start_point}")
        print(f"pages len: {pages_len}")

        while pages_len >= 400:
            start_point += 100
            pages_dict = __pages_dict(start_point)
            pages_len = len(__pages_dict(start_point))
            print(f"new start point: {start_point}")
            print(f"rebuild pages len: {pages_len}")


        if any(i == self.rc_template for i in pages_dict.keys()):
            print(f"Заголовок \"{self.rc_template}\" найден")

            comments = self.get_page_comments(blog_page_id=pages_dict[self.rc_template])
            comments_dict = {
                comment["body"]["storage"]["value"]: comment["id"] for comment in comments["results"]
            }
            #print(comments_dict)

            if not any("Нагрузочное тестирование" in str(i) for i in comments_dict.keys()):
                print("Комментарий не обнаружен, добавляю")
                self.add_comment(blog_page_id=pages_dict[self.rc_template],
                                 text=comment_text)
            else: print("Обнаружен идентичный комментарий, отмена")
        else: print(f"Заголовок \"{self.rc_template}\" не найден")
    

