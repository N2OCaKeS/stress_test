# from libs.libtable import Report
from libs.librating import Report
import numpy as np


report = Report()
total_rating = report.get_total_rating()
print("*******")
print(total_rating)