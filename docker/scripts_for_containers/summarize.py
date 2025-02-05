import time
from random import choice
from threading import Thread

operators = ['+', '*', '-', '/', '//']


def calculator():
    while True:
        num1 = choice(range(1, 10000))
        num2 = choice(range(1, 10000))
        op = choice(operators)

        expression = f"{num1} {op} {num2}"

        result = eval(expression)
        print(result)
        time.sleep(1)


thread = Thread(target=calculator)
thread.start()