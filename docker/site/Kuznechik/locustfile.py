from locust import HttpUser, task, between, TaskSet, LoadTestShape
import random
import time
from collections import namedtuple


# ==== ЗАДАЧИ ПОЛЬЗОВАТЕЛЯ ====

class Tasks(TaskSet):

    @task
    def get_main(self):
        self.client.get("/home", name="GET /home")

# ==== ПОЛЬЗОВАТЕЛЬ ====

class MyUser(HttpUser):
    wait_time = between(1, 3)
    tasks = [Tasks]


# ==== НАГРУЗКА: СТУПЕНЧАТЫЙ РОСТ С УДЕРЖАНИЕМ ====

Step = namedtuple("Step", ["users", "dwell"])  # dwell = время удержания нагрузки (в секундах)
number = 1300
timestep = 150

class StepLoadShape(LoadTestShape):
    """
    6 шагов нагрузки:
    - каждый шаг ждёт, пока достигнуто нужное число пользователей
    - после чего держит нагрузку в течение dwell секунд
    """

    targets_with_times = (
        Step(0, 60),
        Step(number, timestep),
        Step(number*2, timestep),
        Step(number*3, timestep),
        Step(number*4, timestep),
        Step(number*5, timestep),
        Step(number*6, timestep),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.step = 0
        self.time_active = False

    def tick(self):
        if self.step >= len(self.targets_with_times):
            return None  # Завершаем тест

        target = self.targets_with_times[self.step]
        current_users = self.get_current_user_count()

        # Ждём достижения нужного числа пользователей
        if current_users >= target.users:
            if not self.time_active:
                self.reset_time()
                self.time_active = True

            # Проверяем, прошло ли нужное время удержания
            if self.get_run_time() > target.dwell:
                self.step += 1
                self.time_active = False

        return (target.users, number)  # spawn rate

