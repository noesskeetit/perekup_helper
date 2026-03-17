"""AI-агрегатор для перекупов: категоризация авто-объявлений."""

__version__ = "0.1.0"

from perekup_helper.batch import BatchProcessor
from perekup_helper.categorizer import Categorizer
from perekup_helper.models import (
    CarCategory,
    CategoryResult,
    ListingDescription,
    ScoreResult,
)

__all__ = [
    "CarCategory",
    "CategoryResult",
    "ListingDescription",
    "ScoreResult",
    "Categorizer",
    "BatchProcessor",
]
