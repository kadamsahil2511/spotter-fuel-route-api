"""Minimum-cost refueling on a line. Tank starts full and that fuel is not billed."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pump:
    mile: float
    price: float
    station: dict


@dataclass
class Purchase:
    station: dict
    mile: float
    gallons: float
    cost: float
    price: float


@dataclass
class FuelPlan:
    feasible: bool
    purchases: list[Purchase] = field(default_factory=list)
    total_gallons: float = 0.0
    total_cost: float = 0.0
    gap_from: float | None = None
    gap_to: float | None = None

    @property
    def reason(self) -> str | None:
        if self.feasible:
            return None
        return "No station within range"


def plan_fuel(
    pumps: list[Pump],
    destination_miles: float,
    range_miles: float,
    mpg: float,
) -> FuelPlan:
    """Next-cheaper greedy. Origin is mile 0 with a full prepaid tank and no pump."""
    if destination_miles < 0 or range_miles <= 0 or mpg <= 0:
        raise ValueError("distance, range, and mpg must be positive")
    stations = sorted(pumps, key=lambda pump: (pump.mile, pump.price))
    if destination_miles <= range_miles:
        return FuelPlan(feasible=True)

    pos = 0.0
    fuel = float(range_miles)
    price_here = 0.0
    can_buy = False
    current: dict | None = None
    purchases: list[Purchase] = []

    def buy(miles: float) -> None:
        nonlocal fuel
        if miles <= 1e-6 or current is None:
            return
        gallons = miles / mpg
        cost = gallons * price_here
        fuel += miles
        purchases.append(
            Purchase(station=current, mile=pos, gallons=gallons, cost=cost, price=price_here)
        )

    def drive_to(target_mile: float, target_price: float, station: dict) -> None:
        nonlocal pos, fuel, price_here, can_buy, current
        fuel = max(0.0, fuel - (target_mile - pos))
        pos = target_mile
        price_here = target_price
        can_buy = True
        current = station

    guard = 0
    while pos < destination_miles - 1e-6:
        guard += 1
        if guard > len(stations) + 3:
            return FuelPlan(False, gap_from=pos, gap_to=destination_miles)
        if fuel >= destination_miles - pos - 1e-6:
            return _finish(purchases)
        reach = range_miles if can_buy else fuel
        window = [pump for pump in stations if pos + 1e-6 < pump.mile <= pos + reach + 1e-6]
        dest_in = destination_miles <= pos + reach + 1e-6
        cheaper = next((pump for pump in window if pump.price < price_here - 1e-9), None)
        if cheaper is not None and can_buy:
            buy((cheaper.mile - pos) - fuel)
            drive_to(cheaper.mile, cheaper.price, cheaper.station)
        elif cheaper is not None:
            drive_to(cheaper.mile, cheaper.price, cheaper.station)
        elif dest_in and can_buy:
            buy((destination_miles - pos) - fuel)
            return _finish(purchases)
        elif dest_in:
            return _finish(purchases)
        elif window and can_buy:
            buy(range_miles - fuel)
            target = min(window, key=lambda pump: (pump.price, pump.mile))
            drive_to(target.mile, target.price, target.station)
        elif window:
            target = min(window, key=lambda pump: (pump.price, pump.mile))
            drive_to(target.mile, target.price, target.station)
        else:
            furthest = pos + (range_miles if can_buy else fuel)
            ahead = [pump.mile for pump in stations if pump.mile > furthest + 1e-6]
            gap_to = ahead[0] if ahead else destination_miles
            return FuelPlan(feasible=False, gap_from=furthest, gap_to=gap_to)
    return _finish(purchases)


def _finish(purchases: list[Purchase]) -> FuelPlan:
    return FuelPlan(
        feasible=True,
        purchases=purchases,
        total_gallons=sum(item.gallons for item in purchases),
        total_cost=sum(item.cost for item in purchases),
    )
