from allta import Criterion, MathModels

criterions = [
    Criterion(name="read 1mb 1t", values=150495.0, weight=0.5, lower_bound=0, upper_bound=200000, sign=1),
    Criterion(name="write 1mb 1t", values=133122.0, weight=0.5, lower_bound=0, upper_bound=200000, sign=1),
]

criterions = [
    Criterion(name="read 1mb 1t", values=155292.0, weight=0.5, lower_bound=0, upper_bound=200000, sign=1),
    Criterion(name="write 1mb 1t", values=132741.0, weight=0.5, lower_bound=0, upper_bound=200000, sign=1),
]

total_rating, _ = MathModels.total_rating(criteria=criterions)
total_rating_display = round(total_rating * 10000)
print(f"LargeFio total rating: {total_rating_display}")
print(total_rating)