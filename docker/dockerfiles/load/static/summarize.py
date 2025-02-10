import time, datetime
from random import choice
from threading import Thread

operators = ['+', '*', '-', '/', '//']


def calculator():
    while True:
        start = datetime.datetime.now()
        num1 = choice(range(1, 10000))
        num2 = choice(range(1, 10000))
        op = choice(operators)

        expression = f"{num1} {op} {num2}"

        result = eval(expression)
        print(result)
        end = datetime.datetime.now()
        time_repsonse = end - start
        print(time_repsonse.seconds)


thread = Thread(target=calculator)
thread.start()