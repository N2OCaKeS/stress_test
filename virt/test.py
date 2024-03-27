import json

with open('./host_results.txt', 'r') as r:
    host_data = r.read()


result = str(host_data.strip().strip('{,}').replace("'", "").split("% ")).strip("'[]").split(", ")
host_dict = {
    key.split(': ', 1)[0]: key.split(': ', 1)[1] for key in result if len(key) > 10
}

#print(result)
print(host_dict)


with open('./result_testvm1.txt', 'r') as r:
    data = r.read()

print(data)