import time


class Logger:
    def __init__(self,
                 filename='default.log',
                 filemode='w',
                 path='/tmp'):
        
        self.filename = filename
        self.filemode = filemode
        self.path = path

        if self.filemode == 'w':
            with open(f'{self.path}/{self.filename}', 'w') as w:
                w.write('')


    def debug(self, text):
        current_time = time.strftime('%Y%m%d_%H:%M')
        processed_str = f'\n{current_time} - DEBUG - {text}'
        with open(f'{self.path}/{self.filename}', 'a') as debug:
            debug.write(processed_str)

    def error(self, text):
        current_time = time.strftime('%Y%m%d_%H:%M')
        processed_str = f'\n{current_time} - ERROR - {text}'
        with open(f'{self.path}/{self.filename}', 'a') as error:
            error.write(processed_str)

            