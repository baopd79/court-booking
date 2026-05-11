"""Pricing utilities shared across facility and booking modules."""

from decimal import Decimal

from app.modules.facility.models import PricingRule, Slot


def find_price(slot: Slot, rules: list[PricingRule], default_price: Decimal) -> Decimal:
    """Match a slot to its pricing rule by day_of_week + time range."""
    slot_day = slot.slot_start.isoweekday() % 7  # 0=Sun, 1=Mon, ..., 6=Sat
    slot_time = slot.slot_start.time()
    for rule in rules:
        if rule.day_of_week == slot_day and rule.start_time <= slot_time < rule.end_time:
            return rule.price
    return default_price
