from allta import Criterion, MathModels


high_criterions = [
    Criterion(
        name="ever_not_connected_count",
        values=[278],
        weight=0.4,
        sign=1,
        lower_bound=0,
        upper_bound=400,
    ),
    Criterion(
        name="disconnected_count",
        values=[244],
        weight=0.4,
        sign=-1,
        lower_bound=0,
        upper_bound=400,
    ),
    Criterion(
        name="drops_max",
        values=[12],
        weight=0.2,
        sign=-1,
        lower_bound=0,
        upper_bound=400,
    ),
]

print(MathModels.normalize(high_criterions))

criterions = [
    Criterion(
        name="ever_connected_count",
        values=[400],
        weight=0.5,
        sign=1,
        lower_bound=0,
        upper_bound=400,
    ),
    Criterion(
        name="disconnected_count",
        values=[364],
        weight=0.3,
        sign=-1,
        lower_bound=0,
        upper_bound=400
    ),
    Criterion(
        name="drops_max",
        values=[8],
        weight=0.2,
        sign=-1,
        lower_bound=0,
        upper_bound=400,
    ),
]

# high_normalized_criteria = MathModels.normalize(high_criterions)
# normalized_criteria = MathModels.normalize(criterions)

high_total_rating, s = MathModels.total_rating(criteria=high_criterions, normalize=True)
high_total_rating = int(round(high_total_rating*10))
print(high_total_rating)  # красивое число ~700–900
total_rating, s = MathModels.total_rating(criteria=criterions)
total_rating = int(round(total_rating*10))
print(total_rating)  # красивое число ~700–900

# print(high_total_rating / total_rating)

