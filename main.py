"""CLI entry point for Avito auto parser."""

import argparse
import asyncio
import logging
import signal
import sys

from src.avito_parser.listing_parser import SearchFilters
from src.avito_parser.pipeline import scrape_and_save
from src.avito_parser.scheduler import start_scheduler, stop_scheduler


def main():
    parser = argparse.ArgumentParser(description="Avito auto ads parser")
    parser.add_argument("--brand", help="Car brand filter (e.g. toyota)")
    parser.add_argument("--model", help="Car model filter (e.g. camry)")
    parser.add_argument("--year-from", type=int, help="Minimum year")
    parser.add_argument("--year-to", type=int, help="Maximum year")
    parser.add_argument("--price-from", type=int, help="Minimum price")
    parser.add_argument("--price-to", type=int, help="Maximum price")
    parser.add_argument("--location", default="rossiya", help="Location slug (default: rossiya)")
    parser.add_argument("--daemon", action="store_true", help="Run as periodic daemon")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    filters = SearchFilters(
        brand=args.brand,
        model=args.model,
        year_from=args.year_from,
        year_to=args.year_to,
        price_from=args.price_from,
        price_to=args.price_to,
        location_slug=args.location,
    )

    if args.daemon:
        logging.info("Starting periodic scraper daemon...")

        # Run once immediately
        count = asyncio.run(scrape_and_save(filters))
        logging.info("Initial scrape done: %d ads", count)

        start_scheduler(filters)

        def handle_signal(signum, frame):
            logging.info("Received signal %d, shutting down...", signum)
            stop_scheduler()
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        # Keep main thread alive
        try:
            signal.pause()
        except AttributeError:
            # Windows fallback
            import time

            while True:
                time.sleep(60)
    else:
        count = asyncio.run(scrape_and_save(filters))
        logging.info("Scraping complete: %d ads saved/updated", count)


if __name__ == "__main__":
    main()
