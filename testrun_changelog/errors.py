# class ChangelogNotAvailable(Exception):
#     """Исключение при недоступности changelog по сгенерированной ссылке"""
#     pass

class RepositoryNotAvailableFromAllta(Exception):
    """Исключение при недоступности файла с репозиториями из allta"""
    pass
    
class PackagesNotFound(Exception):
    """Исключение при недоступности packages конкретного компонента по сгенирированной ссылке"""
    pass