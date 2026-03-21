"""Allow running seed as ``python -m scripts.seed``."""

import asyncio

from scripts.seed import main

if __name__ == "__main__":
    asyncio.run(main())
