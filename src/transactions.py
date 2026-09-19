"""Shared transaction normalization and identity rules for every ingestion path."""
from __future__ import annotations
import hashlib
import json
import pandas as pd

NULL_MARKERS = {"", "-", "none", "nonetype", "nan", "null", "nat"}
TEXT_COLUMNS = ("dealYear", "dealMonth", "dealDay", "sggCd", "regionName", "umdNm", "jibun", "aptNm", "buildYear", "dealType", "cdealType", "cdealDay", "aptDong", "rgstDate")
BASE_IDENTITY_COLUMNS = ("dealYear", "dealMonth", "dealDay", "sggCd", "umdNm", "jibun", "aptNm", "floor", "excluUseAr")

def null_if_missing(value):
    if value is None or pd.isna(value): return None
    text = str(value).strip()
    return None if text.lower() in NULL_MARKERS else text

def _canonical(value):
    value = null_if_missing(value)
    if value is None: return ""
    if isinstance(value, float): return format(value, ".6f").rstrip("0").rstrip(".")
    return str(value)

def build_transaction_key(row: pd.Series) -> str:
    """Use stable rich fields when present; keep price in the legacy fallback."""
    rich = any(null_if_missing(row.get(name)) for name in ("aptDong", "rgstDate"))
    columns = BASE_IDENTITY_COLUMNS + (("aptDong", "rgstDate") if rich else ("dealAmount",))
    payload = [_canonical(row.get(name)) for name in columns]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

def normalize_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize API, CSV and direct DB inputs to one representation."""
    if df.empty: return df.copy()
    result = df.copy()
    for column in TEXT_COLUMNS:
        if column not in result: result[column] = None
        result[column] = result[column].map(null_if_missing)
    result["dealType"] = result["dealType"].fillna("중개거래")
    result["cdealType"] = result["cdealType"].map(lambda v: "O" if null_if_missing(v) in {"O", "0", "취소", "해제"} else None)
    result["cdealDay"] = result["cdealDay"].map(null_if_missing)
    result.loc[result["cdealType"].isna(), "cdealDay"] = None
    for column in ("dealMonth", "dealDay"):
        result[column] = result[column].map(lambda v: str(v).zfill(2) if v is not None else None)
    for column in ("dealAmount", "floor"):
        if column in result: result[column] = pd.to_numeric(result[column], errors="coerce").astype("Int64")
    if "excluUseAr" in result: result["excluUseAr"] = pd.to_numeric(result["excluUseAr"], errors="coerce")
    generated = pd.to_datetime(result["dealYear"].fillna("") + "-" + result["dealMonth"].fillna("") + "-" + result["dealDay"].fillna(""), errors="coerce")
    if "dealDate" not in result: result["dealDate"] = generated
    else: result["dealDate"] = pd.to_datetime(result["dealDate"], errors="coerce").fillna(generated)
    result["transactionKey"] = result.apply(build_transaction_key, axis=1)
    return result
