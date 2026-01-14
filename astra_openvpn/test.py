from allta import MathModels, Criterion
from sklearn.preprocessing import MinMaxScaler

data1 = [224, 0, 400]
data2 = [176, 0, 400]
data3 = [0, 0, 400]



data = [Criterion(name='1', values=[data1[0]], lower_bound=0, upper_bound=400, weight=0.4, sign=1),
        Criterion(name='2', values=[data2[0]], lower_bound=0, upper_bound=400, weight=0.4, sign=-1),
        Criterion(name='3', values=[data3[0]], lower_bound=0, upper_bound=400, weight=0.2, sign=-1)]

print(MathModels.total_rating(data))

scaler = MinMaxScaler(feature_range=(0, 1))

data_2d = [[x] for x in data1]
normalized1 = scaler.fit_transform(data_2d)
normalized1 = normalized1.flatten()

data_2d = [[x] for x in data2]
normalized2 = scaler.fit_transform(data_2d)
normalized2 = normalized2.flatten()

data_2d = [[x] for x in data3]
normalized3 = scaler.fit_transform(data_2d)
normalized3 = normalized3.flatten()

total = 0.4 * normalized1[0] + 0.4 / (normalized2[0]+0.001) + 0.2 / (normalized3[0]+0.001)
print(total)