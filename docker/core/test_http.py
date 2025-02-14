# import time 
# from locust import HttpUser, task, between, run_single_user


# class WebsiteUser(HttpUser):
#     wait_time = between(1, 5)
#     host = "http://172.21.0.2:80"

#     @task
#     def index_page(self):
#         self.client.get(url="/index.html")

#     @task(2)
#     def slow_page(self):
#         self.client.get(url="/")


# if __name__ == "__main__":
#     run_single_user(WebsiteUser)