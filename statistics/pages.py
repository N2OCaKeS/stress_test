from confluence.confluence import ConfluencePage
from logging_conf import main_logger

class Pages:
    def __init__(self, username, token) -> None:
        self.username = username
        self.token = token
        self.CP = ConfluencePage(username=self.username, token=self.token)
        main_logger.info(f"Отработал конструктор {self.__class__.__name__}. Должно быть подключение к Confluence")
    
    """
        TODO ДОРАБОТАТЬ!!!
        УБРАТЬ 3-х уровневую вложенность цикла
    """
    def get_list_pages(self, id_root_page, stat_title):
        """
            Получаем дочерние страницы 1.7: 1.7.1; 1.7.2; 1.7.n...
        """
        children_main_page = self.CP.get_child_page_as_html(id=id_root_page, by_title=False)
        main_logger.debug("Получаем дочерние страницы 1.7: 1.7.1; 1.7.2; 1.7.n...")
        required_pages = [] #, required_pages_rc = [], []
        test_dict_rc = {}
        """
            required_page - ID родительской страницы в каждой версии, в которой находится список отчетов
        """
        required_pages = []
        for id_children_from_main_page in children_main_page:
            """
                Получаем ID страниц PostgreSQL, Системные службы, Файловые системы в каждой конкретной версии
            """
            try:
                name_page_original = self.CP.get_page_as_html(id=id_children_from_main_page).get("title")
                name_page = self.CP.get_page_as_html(id=id_children_from_main_page).get("title").split(" ⬝ ")[1]
                # print(name_page)
            except IndexError:
                pass

            astra_version_child_pages = self.CP.get_child_page_as_html(id=id_children_from_main_page, by_title=False)
            temp_arr = []
            """
                Проходим по каждому полученному ID
            """
            for page_id in astra_version_child_pages:
                """
                    Получаем информацию о странице
                """
                page = self.CP.get_page_as_html(id=page_id)
                """
                    Проверяем есть ли в заголовке Файловые системы и имеются ли дочерние страницы
                """
                if stat_title in page.get("title") and self.CP.get_child_page_as_html(id=page_id):
                    required_pages.append(page_id)
                
                """
                    Статистика для RC
                """
                try:
                    name_child_page = page.get("title").split(" ⬝ ")[1]
                except IndexError:
                    pass
                if "1.7" in name_child_page or "1.8" in name_child_page:
                    hz_kak_nazvat_pages = self.CP.get_child_page_as_html(id=page_id, by_title=False)
                    if hz_kak_nazvat_pages:
                        for item_page in hz_kak_nazvat_pages:
                            if stat_title in self.CP.get_page_as_html(id=item_page).get("title"):
                                temp_arr.append(item_page)

            test_dict_rc[name_page_original] = temp_arr

        return required_pages, test_dict_rc