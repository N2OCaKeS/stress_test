from locust import HttpUser, task, between, TaskSet
import random

class Tasks(TaskSet):

    @task
    def create_single_message_json(self):
        payload = {"content": "Test Json Message"}
        headers = {"Content-Type": "application/json"}
        self.client.post("/message/create", name="POST JSON MESSAGE", json=payload, headers=headers)

    @task
    def create_bulk_messages_json(self):
        num_messages = random.randint(5, 2002)
        payload = [{"content": f"Bulk Message {i}"} for i in range(num_messages)]
        headers = {"Content-Type": "application/json"}
        self.client.post("/message/create", name="POST BULK MESSAGE", json=payload, headers=headers)

    @task
    def create_message_form(self):
        data = {"content": "Test Form Message"}
        self.client.post("/message/create", name="POST FORM", data=data)

    @task
    def get_main(self):
        self.client.get("/home", name="GET /home")

    @task
    def get_create(self):
        self.client.get("/message/create", name="GET /message/create")


class MyUser(HttpUser):
    wait_time = between(1, 3)
    tasks = [Tasks]


# class AdminTasks(SequentialTaskSet):
#     wait_time = between(1, 3)
#     sign_in = False

#     @task
#     def register(self):
#         registration_data = {
#             "fio": "admin",  
#             "login": "admin",    
#             "password": "1234",
#             "confirm_password": "1234"
#         }
#         response = self.client.post("/user/register", data=registration_data)
#         if response.status_code == 200:
#             print("Регистрация прошла успешно.")
#         else:
#             print("Ошибка при регистрации.")

#     @task
#     def login(self):
#         login_data = {
#             "login": "admin",
#             "password": "1234"
#         }
#         response = self.client.post("/user/login", data=login_data)
#         if response.status_code == 200:
#             print("Авторизация прошла успешно.")
#             self.sign_in = True
#         else:
#             print("Ошибка при авторизации.")

#     @task
#     def create_message(self):
#         if self.sign_in:  # Печатает сообщение только если пользователь авторизован
#             message_data = {"content": "Admin Message"}
#             self.client.post("/message/create", json=message_data)
#         else:
#             print("Ошибка: Администратор не авторизован!")


# class Admin(HttpUser):
#     wait_time = between(1, 3)
#     host = HOST
#     tasks = [AdminTasks]
