import time
from functools import wraps

class BaseDecorators():
    """
    Класс с базовыми декораторами.

    Основные функции:
    - trycorator: Декоратор для оборачивания функции в блок try-except.
    """
    def trycorator(function):
        """
        Данный декоратор оборачивает функцию в try except
        """
        def wrapper(*args, **kwargs):
            try: 
                result = function(*args, **kwargs)
                return result
            except Exception as e:
                print(f'Function: {function.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')
                return result

        return wrapper
    
    # def timer(function):
    #     "Оборачивает функцию в таймер и выводит время выполнения функции"
    #     @wraps(function)
    #     def wrapper(*args, **kwargs):
    #         t0 = time.perf_counter()
    #         result = function(*args, **kwargs)
    #         dt = (time.perf_counter() - t0) * 1000
    #         print(f"{function.__name__}: {dt:.2f} ms")
    #         return result
    #     return wrapper        
        
