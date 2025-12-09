from dataclasses import dataclass


@dataclass(slots=True)
class ChartPoint:
    # The x-coordinate of the point, expressed as a bar index value
    index: int

    # The x-coordinate of the point, expressed as a UNIX time value, in milliseconds
    time: int

    # The y-coordinate of the point
    price: float
