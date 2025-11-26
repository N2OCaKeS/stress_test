from allta import Criterion, MathModels


def demo_normalize():
    values = [10, 20, 30, 40]
    print("normalize:", MathModels.normalize(values, lower=15, upper=35))


def demo_approximate():
    values = [1, 4, 9, 16]
    f_linear = MathModels.approximate(values, kind="linear")
    f_interp = MathModels.approximate(values, kind="interp")
    print("approx linear at 2.5:", f_linear(2.5))
    print("approx interp at 2.5:", f_interp(2.5))


def demo_total_rating():
    criteria = [
        Criterion(name="latency", values=[120], weight=0.25, sign=-1, lower_bound=0),
        Criterion(name="throughput", values=[800], weight=0.375, sign=1),
        Criterion(name="files_per_sec", values=[3], weight=0.375, sign=1),
    ]
    rating_mean, contrib_mean = MathModels.total_rating(criteria, aggregate="mean", normalize=False)
    rating_last, contrib_last = MathModels.total_rating(criteria, aggregate="last", normalize=False)
    rating_max, contrib_max = MathModels.total_rating(criteria, aggregate="max", normalize=False)

    print("rating (mean):", rating_mean, "contrib:", contrib_mean)
    print("rating (last):", rating_last, "contrib:", contrib_last)
    print("rating (max):", rating_max, "contrib:", contrib_max)


def demo_to_dict():
    criteria = [
        Criterion(name="cpu", values=[0.2, 0.4, 0.3], weight=0.5, sign=1),
        Criterion(name="errors", values=[5, 2, 1], weight=0.5, sign=-1, lower_bound=0),
    ]
    print("as dict (mean):", MathModels.to_dict(criteria, aggregate="mean", normalize=True))
    print("as dict (last):", MathModels.to_dict(criteria, aggregate="last", normalize=True))


if __name__ == "__main__":
    demo_normalize()
    demo_approximate()
    demo_total_rating()
    demo_to_dict()
