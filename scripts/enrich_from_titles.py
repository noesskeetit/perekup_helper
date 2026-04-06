"""Enrich listings by parsing data from titles — no HTTP requests needed.

Avito titles: "Toyota Camry 2.4 AT, 2007, 200 000 км"
Contains: engine_volume, transmission (AT/MT/CVT/AMT), year, mileage
"""

import asyncio
import re
import sys

sys.path.insert(0, ".")

TRANSMISSION_MAP = {
    "AT": "АКПП",
    "MT": "МКПП",
    "CVT": "Вариатор",
    "AMT": "Робот",
}


def parse_title(title: str) -> dict:
    """Extract car specs from Avito title string."""
    result = {}

    # Engine volume: "2.4" before AT/MT/CVT/AMT
    m = re.search(r"(\d+\.\d+)\s*(AT|MT|CVT|AMT)", title)
    if m:
        result["engine_volume"] = float(m.group(1))
        result["transmission"] = TRANSMISSION_MAP.get(m.group(2), m.group(2))

    # Mileage: "200 000 км" or "200000км"
    m = re.search(r"(\d[\d\s\xa0]+)\s*(?:км|km)", title)
    if m:
        digits = re.sub(r"[\s\xa0]", "", m.group(1))
        if digits.isdigit():
            result["mileage"] = int(digits)

    return result


async def main():
    from sqlalchemy import select

    from app.db.session import async_session_factory, engine
    from app.models.listing import Listing

    async with async_session_factory() as session:
        rows = (await session.execute(
            select(Listing).where(Listing.source == "avito")
        )).scalars().all()

        updated = 0
        for listing in rows:
            raw = listing.raw_data or {}
            title = raw.get("title", "")
            if not title:
                continue

            parsed = parse_title(title)
            changed = False

            if "mileage" in parsed and listing.mileage is None:
                listing.mileage = parsed["mileage"]
                changed = True
            if "engine_volume" in parsed and listing.engine_volume is None:
                listing.engine_volume = parsed["engine_volume"]
                changed = True
            if "transmission" in parsed and listing.transmission is None:
                listing.transmission = parsed["transmission"]
                changed = True

            if changed:
                updated += 1

        await session.commit()
        print(f"Updated {updated}/{len(rows)} listings from titles")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
