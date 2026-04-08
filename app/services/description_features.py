"""Extract pricing-relevant features from listing descriptions.

Two approaches:
1. Keyword extraction — fast, interpretable markers (e.g., "битая", "один хозяин")
2. TF-IDF + SVD — dense numeric features capturing description semantics

Both produce numeric columns that can be added to the pricing model.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer

# ── Keyword patterns ─────────────────────────────────────────────────
# Each pattern maps to a binary feature. Regex is case-insensitive.
# Positive = likely higher price; negative = likely lower price.

KEYWORD_PATTERNS: dict[str, str] = {
    # Positive condition markers
    "kw_not_damaged": r"не\s*бит|без\s*дтп|без\s*аварий|не\s*краш",
    "kw_one_owner": r"один\s*(?:хозяин|владел)|1\s*(?:хозяин|владел)|первый\s*владел",
    "kw_original_mileage": r"оригинальн\w*\s*пробег|подтвержд\w*\s*пробег|реальн\w*\s*пробег",
    "kw_garage_kept": r"гаражн\w*\s*хранен|в\s*гараже",
    "kw_good_condition": r"идеальн\w*\s*состоян|отличн\w*\s*состоян|хорош\w*\s*состоян",
    "kw_original_paint": r"родн\w*\s*(?:краск|окрас|цвет)|оригинальн\w*\s*(?:краск|окрас)",
    "kw_original_pts": r"оригинал\w*\s*птс|птс\s*оригинал",
    "kw_serviced": r"обслужен|прошл?\w*\s*то|замен\w*\s*масл|регулярн\w*\s*то",
    "kw_warranty": r"гарантия|на\s*гарантии|дилерск\w*\s*гарант",
    "kw_full_package": r"максимальн\w*\s*комплект|полн\w*\s*комплект|топов\w*\s*комплект",
    # Negative condition markers
    "kw_damaged": r"(?<!не\s)бит\w{0,3}(?:\s|$|,)|после\s*дтп|аварийн\w*|удар\w*\s*в\s",
    "kw_duplicate_pts": r"дубликат\w*\s*птс|птс\s*дубликат",
    "kw_many_owners": r"(?:[3-9]|1[0-9])\s*(?:хозяин|владел)|много\s*владел",
    "kw_needs_repair": r"требует\s*(?:ремонт|вложен)|нужен\s*ремонт|разобран",
    "kw_rust": r"(?:ржав|коррози|гнил)\w*|сквозн\w*\s*(?:дыр|ржав)",
    "kw_high_mileage": r"большой\s*пробег|высок\w*\s*пробег",
    # Dealer markers
    "kw_dealer": r"автосалон|автодилер|кредит\w*\s*(?:ставк|платеж|взнос)|trade.?in|трейд.?ин",
    "kw_credit": r"кредит|рассрочк|ежемесячн\w*\s*платеж",
    "kw_urgent": r"срочно|торг\w*(?:$|\s|!)|быстр\w*\s*продаж",
    # Equipment markers
    "kw_leather": r"кож\w*\s*(?:салон|сиден)|натуральн\w*\s*кож",
    "kw_climate": r"климат.?контрол|двухзонн\w*\s*климат",
    "kw_heated_seats": r"подогрев\w*\s*сиден|обогрев\w*\s*сиден",
    "kw_camera": r"камер\w*\s*(?:задн|парков)|парктроник",
}

# Pre-compile patterns
_COMPILED_PATTERNS = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in KEYWORD_PATTERNS.items()}


def extract_keywords(descriptions: pd.Series) -> pd.DataFrame:
    """Extract binary keyword features from description texts.

    Args:
        descriptions: Series of Russian description strings.

    Returns:
        DataFrame with one bool column per keyword pattern.
    """
    results = {}
    texts = descriptions.fillna("")
    for name, pat in _COMPILED_PATTERNS.items():
        results[name] = texts.apply(pat.search).astype(bool)

    return pd.DataFrame(results, index=descriptions.index).astype(int)


# ── TF-IDF Features ─────────────────────────────────────────────────

# Simple Russian tokenizer: lowercase, remove non-alpha, split
_CYRILLIC_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)

# Common Russian stop words relevant to car listings
_STOP_WORDS = frozenset(
    {
        "и",
        "в",
        "на",
        "с",
        "по",
        "для",
        "от",
        "до",
        "не",
        "из",
        "за",
        "к",
        "о",
        "у",
        "как",
        "все",
        "что",
        "это",
        "так",
        "или",
        "но",
        "а",
        "при",
        "уже",
        "если",
        "вы",
        "мы",
        "он",
        "она",
        "вас",
        "вам",
        "нас",
        "нам",
        "его",
        "её",
        "был",
        "была",
        "были",
        "быть",
        "есть",
        "будет",
        "также",
        "только",
        "можно",
        "нужно",
        "очень",
        "более",
        "менее",
        "автомобиль",
        "авто",
        "машина",
        "продаж",
        "продажа",
    }
)


def _tokenize(text: str) -> str:
    """Tokenize Russian text for TF-IDF."""
    tokens = _CYRILLIC_TOKEN_RE.findall(text.lower())
    return " ".join(t for t in tokens if t not in _STOP_WORDS and len(t) > 2)


class DescriptionTfidf:
    """TF-IDF + SVD feature extractor for car listing descriptions.

    Fits on training data, transforms any text to N_COMPONENTS dense features.
    """

    def __init__(self, n_components: int = 20, max_features: int = 5000):
        self.n_components = n_components
        self.vectorizer = TfidfVectorizer(
            max_features=max_features,
            min_df=5,
            max_df=0.8,
            ngram_range=(1, 2),
            preprocessor=_tokenize,
            token_pattern=r"(?u)\b\w+\b",
        )
        self.svd = TruncatedSVD(n_components=n_components, random_state=42)
        self._fitted = False

    def fit(self, descriptions: pd.Series) -> DescriptionTfidf:
        """Fit TF-IDF + SVD on training descriptions."""
        texts = descriptions.fillna("").values
        tfidf_matrix = self.vectorizer.fit_transform(texts)
        self.svd.fit(tfidf_matrix)
        self._fitted = True
        return self

    def transform(self, descriptions: pd.Series) -> pd.DataFrame:
        """Transform descriptions to dense features."""
        if not self._fitted:
            raise RuntimeError("Must call fit() first")

        texts = descriptions.fillna("").values
        tfidf_matrix = self.vectorizer.transform(texts)
        svd_features = self.svd.transform(tfidf_matrix)

        columns = [f"tfidf_{i}" for i in range(self.n_components)]
        return pd.DataFrame(svd_features, columns=columns, index=descriptions.index)

    def fit_transform(self, descriptions: pd.Series) -> pd.DataFrame:
        """Fit and transform in one step."""
        self.fit(descriptions)
        return self.transform(descriptions)

    @property
    def explained_variance_ratio(self) -> np.ndarray:
        """Cumulative explained variance ratio of SVD components."""
        return np.cumsum(self.svd.explained_variance_ratio_)
