

class BaseDecorators():
    """Класс с базовыми декораторами
    """
    def trycorator(function):
        """
        Данный декоратор оборачивает функцию в try except
        """
        def wrapper(*args, **kwargs):
            try: 
                function(*args, **kwargs)
            except Exception as e:
                print(f'Function: {function.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')

        return wrapper
