# from locust import HttpUser, task, constant, between, SequentialTaskSet
# import time

# class MainUser(HttpUser):
#     wait_time = between(1, 2)
#     host = "http://172.21.0.2"
#     tasks = [TaskQueue]


# class TaskQueue(SequentialTaskSet):
    

#     @task
#     def getter(self):
#         self.client.get("/", name="GET_MAIN_PAGE")
#         self.wait()


#     @task
#     def poster(self):
#         data = ["test_key": "test_value"]
#         self.client.post


# class QuickstartUser(HttpUser):
#     wait_time = between(1, 5)

#     @task
#     def hello_world(self):
#         self.client.get("/hello")
#         self.client.get("/world")

#     @task(3)
#     def view_items(self):
#         for item_id in range(10):
#             self.client.get(f"/item?id={item_id}", name="/item")
#             time.sleep()

#     def on_start(self):
#         self.client.post("/login", json={"username": "foo", "password": "bar"})