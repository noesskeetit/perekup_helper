"""Calculate price deviation from market value."""


def calculate_price_deviation(price: int | None, market_price: int | None) -> float | None:
    """
    Calculate price deviation from market price in percent.

    Returns negative values when price is below market (good deal),
    positive when above market (overpriced).

    Example: price=800000, market=1000000 -> -20.0 (20% below market)
    """
    if not price or not market_price or market_price == 0:
        return None

    deviation = ((price - market_price) / market_price) * 100
    return round(deviation, 2)
