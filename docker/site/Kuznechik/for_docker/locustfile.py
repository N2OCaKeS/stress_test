from locust import HttpUser, task, between, SequentialTaskSet
import json


# class PostJsonUser(HttpUser):
#     wait_time = between(1, 3)
#     host = HOST

#     @task
#     def create_message(self):
#         payload = {"content": "Test Json Message"}
#         self.client.post("/message/create", json=payload)


# class PostFormUser(HttpUser):
#     wait_time = between(1, 3)

#     @task
#     def create_message(self):
#         data = {"content": "Test Form Message"}
#         self.client.post("/message/create", data=data)


class GetUser(HttpUser):
    wait_time = between(1, 3)

    @task(2)
    def get_main(self):
        self.client.get("/", name="GET /")

    
    @task(1)
    def create_message(self):
        payload = {"content": "Test Json Message"}
        self.client.post("/message/create", json=payload, name="POST message_json on /message/create")


    @task(1)
    def create_message(self):
        data = {"content": "Test Form Message"}
        self.client.post("/message/create", data=data, name="POST message_form on /message/create")



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