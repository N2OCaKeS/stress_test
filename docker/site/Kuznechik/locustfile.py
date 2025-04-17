from locust import HttpUser, task, between, TaskSet, LoadTestShape
import random
import time
import sys
import os
from collections import namedtuple
from allta import GetEnv


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

# Подключаем .env
try:
    GetEnv.activate_absolute("/home/u/git/stress_test/docker/site/.env")
except:
    GetEnv.activate_absolute("/app/.env")

users_count = int(GetEnv.get("USERS_PER_SEC"))
timestep = int(GetEnv.get("TIMESTEP"))

Step = namedtuple("Step", ["users", "dwell"])  # dwell = время удержания нагрузки

class StepLoadShape(LoadTestShape):
    """
    Ступенчатая нагрузка: 6 шагов с удержанием, строгое завершение.
    """

    targets_with_times = (
        Step(0, 60),
        Step(users_count, timestep),
        Step(users_count*2, timestep),
        Step(users_count*3, timestep),
        Step(users_count*4, timestep),
        Step(users_count*5, timestep),
        Step(users_count*6, timestep),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.step = 0
        self.time_active = False

    def tick(self):
        if self.step >= len(self.targets_with_times):
            return None  # Строгое завершение теста

        target = self.targets_with_times[self.step]
        current_users = self.get_current_user_count()

        # Ждём достижения нужного количества пользователей
        if current_users >= target.users:
            if not self.time_active:
                self.reset_time()
                self.time_active = True

            # Если выдержано время, переходим к следующему шагу
            if self.get_run_time() > target.dwell:
                print(f"[StepLoad] Завершён шаг {self.step + 1}: {target.users} пользователей, удержание {target.dwell} сек")
                self.step += 1
                self.time_active = False

        # Возвращаем текущее целевое значение пользователей и скорость запуска
        return (target.users, users_count)
