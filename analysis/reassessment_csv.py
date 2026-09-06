"""Read MOLIT CSV snapshots without the database's lossy UPSERT step.

A canceled report and an active report may share the public natural key. Within
one source snapshot the active report is retained, including its building and
registration evidence. This is not a reconstruction of status at ``as_of``:
``as_of`` limits contract dates in the currently supplied snapshots.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_csv import parse_and_transform, read_molit_csv


NAMES = ("매교역푸르지오SKVIEW", "수원센트럴아이파크자이")
KEY = ["dealDate", "sggCd", "umdNm", "jibun", "aptNm", "floor", "excluUseAr", "dealAmount"]
EMPTY = {"", "-", "none", "null", "nan"}
DB_COLUMNS = [
    "id", "dealDate", "dealYear", "dealMonth", "dealDay", "sggCd", "regionName",
    "umdNm", "jibun", "aptNm", "floor", "excluUseAr", "areaType", "dealAmount",
    "buildYear", "dealType", "cdealType", "cdealDay", "updated_at",
]
SOURCE_COLUMNS = ["source_file", "source_row", "source_no", "source_sha256", "building",
                  "registrationDate", "cancelled", "duplicate_count"]


def _absent(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype(str).str.strip().str.lower().isin(EMPTY)


def _source_column(raw: pd.DataFrame, name: str) -> pd.Series:
    result = raw[name].astype("object") if name in raw else pd.Series(None, index=raw.index, dtype=object)
    result = result.map(lambda value: str(value).strip() if pd.notna(value) else None)
    return result.mask(_absent(result), None)


def _declared_coverage(payload: bytes) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    for encoding in ("cp949", "euc-kr", "utf-8-sig", "utf-8"):
        try:
            content = payload.decode(encoding)
        except UnicodeDecodeError:
            continue
        match = re.search(r"계약일자\s*:\s*(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", content)
        if match:
            left, right = (pd.Timestamp(value) for value in match.groups())
            if left > right:
                raise ValueError("CSV의 계약일자 검색 범위가 역순입니다.")
            return left, right
    return None


def load_csv_snapshot(
    csv_dir: Path, start: pd.Timestamp, as_of: pd.Timestamp,
) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Return active transactions, a JSON-safe audit, and source audit records.

    ``source_row`` is the one-based data-record position after the CSV header;
    ``source_no`` preserves MOLIT's NO column. ``registrationDate`` preserves
    the original text rather than guessing a century from two-digit years.

    Byte-identical copied files are read once. Differing files with overlapping
    requested-period coverage fail closed: combining historical snapshots can
    revive a subsequently canceled report. Declared search dates are used when
    available; otherwise the full file's observed contract-date range is used.
    Distinct files must have known, non-overlapping coverage. No filename or
    filesystem timestamp is assumed to prove which snapshot supersedes another.

    The third result contains all excluded canceled rows, their active same-key
    counterparts, and active duplicate candidates. Cancellation is filtered
    before active-key deduplication only within these non-overlapping snapshots.
    """
    start, as_of = pd.Timestamp(start).normalize(), pd.Timestamp(as_of).normalize()
    if start > as_of:
        raise ValueError("분석 시작일은 기준일보다 늦을 수 없습니다.")
    paths = sorted(path for path in Path(csv_dir).iterdir() if path.is_file() and path.suffix.lower() == ".csv")
    if not paths:
        raise ValueError(f"CSV 파일이 없습니다: {csv_dir}")
    audit = {
        "source": "MOLIT CSV snapshots", "files": [], "raw_target_rows": 0,
        "invalid_date_rows": 0, "outside_date_rows": 0, "invalid_price_rows": 0,
        "invalid_area_rows": 0,
        "invalid_floor_rows": 0, "identical_files_skipped": 0,
        "snapshot_policy": "동일 SHA-256 파일은 한 번만 사용; 서로 다른 파일의 기간 중첩은 오류 처리",
        "cancellation_policy": "같은 원본 스냅샷의 해제 행을 먼저 제외한 뒤 유효 행의 자연키 중복 제거",
        "date_policy": "기준일은 계약일의 상한이며 당시 공개 상태를 복원하는 기준일이 아님",
        "source_row_policy": "CSV 헤더 이후 첫 데이터 행을 1로 세는 행 번호; NO는 source_no에 별도 보존",
    }
    seen_hashes: dict[str, str] = {}
    covered: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
    frames = []
    for path in paths:
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        meta = {"file": path.name, "sha256": digest, "bytes": len(payload)}
        audit["files"].append(meta)
        if digest in seen_hashes:
            meta.update(status="identical_copy_skipped", identical_to=seen_hashes[digest])
            audit["identical_files_skipped"] += 1
            continue
        seen_hashes[digest] = path.name
        raw = read_molit_csv(str(path))
        raw.columns = raw.columns.str.strip()
        required = {"단지명", "시군구", "번지", "전용면적(㎡)", "계약년월", "계약일", "거래금액(만원)", "해제사유발생일"}
        missing = required - set(raw.columns)
        if missing:
            raise ValueError(f"CSV 필수 열이 없습니다: {path.name}: {', '.join(sorted(missing))}")
        prices = pd.to_numeric(raw["거래금액(만원)"].str.replace(",", "", regex=False).str.strip(), errors="coerce")
        price_valid = np.isfinite(prices) & prices.gt(0) & prices.mod(1).eq(0)
        # Transform a copy: the reusable importer strips/normalizes its input.
        transform_input = raw.copy()
        # The old importer casts amounts to int eagerly. Use a placeholder only
        # for parsing, then restore numeric values and audit/exclude invalid rows.
        transform_input["거래금액(만원)"] = prices.where(price_valid, 0).astype(str)
        if "층" in raw:
            parsed_floors = pd.to_numeric(raw["층"], errors="coerce")
            transform_input["층"] = parsed_floors.where(np.isfinite(parsed_floors), 0).astype(str)
        transformed = parse_and_transform(transform_input)
        transformed["dealAmount"] = prices
        if transformed.empty:
            raise ValueError(f"CSV에서 계약일 자료를 읽을 수 없습니다: {path.name}")
        # The old parser removes non-digits; malformed tokens must not silently
        # become plausible dates (for example "202x503" -> "202503").
        year_month = raw["계약년월"].astype(str).str.strip()
        day = raw["계약일"].astype(str).str.strip()
        valid_tokens = year_month.str.fullmatch(r"\d{6}") & day.str.fullmatch(r"\d{1,2}")
        dates = year_month.str[:4] + "-" + year_month.str[4:] + "-" + day.str.zfill(2)
        transformed["dealDate"] = pd.to_datetime(dates.where(valid_tokens), format="%Y-%m-%d", errors="coerce")
        coverage = _declared_coverage(payload)
        declared = coverage is not None
        all_dates = transformed["dealDate"].dropna()
        if coverage is None and not all_dates.empty:
            coverage = (all_dates.min(), all_dates.max())
        if coverage is None:
            raise ValueError(f"CSV의 계약일 검색 범위/관측 범위를 알 수 없습니다: {path.name}")
        meta.update(coverage_start=coverage[0].strftime("%Y-%m-%d"),
                    coverage_end=coverage[1].strftime("%Y-%m-%d"),
                    coverage_basis="declared_search_period" if declared else "observed_dates",
                    raw_rows=len(raw), status="read")
        if declared and not all_dates.between(*coverage).all():
            raise ValueError(f"CSV 관측 계약일이 선언된 검색 범위를 벗어납니다: {path.name}")
        left, right = max(start, coverage[0]), min(as_of, coverage[1])
        if left <= right:
            for other_name, other_left, other_right in covered:
                if max(left, other_left) <= min(right, other_right):
                    raise ValueError(
                        f"서로 다른 CSV 스냅샷의 계약일 범위가 중첩됩니다: {other_name}, {path.name}. "
                        "같은 기간의 최신 원본 하나를 지정한 디렉터리를 사용하세요."
                    )
            covered.append((path.name, left, right))
        selected = transformed["aptNm"].isin(NAMES) & transformed["excluUseAr"].ge(84) & transformed["excluUseAr"].lt(85)
        audit["invalid_area_rows"] += int((transformed["aptNm"].isin(NAMES) & ~np.isfinite(transformed["excluUseAr"])).sum())
        df = transformed[selected].copy()
        audit["raw_target_rows"] += len(df)
        meta["raw_target_rows"] = len(df)
        audit["invalid_date_rows"] += int(df["dealDate"].isna().sum())
        in_period = df["dealDate"].between(start, as_of)
        audit["outside_date_rows"] += int((df["dealDate"].notna() & ~in_period).sum())
        df = df[in_period].copy()
        df["source_file"] = path.name
        df["source_row"] = df.index + 1
        df["source_no"] = _source_column(raw, "NO").reindex(df.index)
        df["source_sha256"] = digest
        df["building"] = _source_column(raw, "동").reindex(df.index)
        df["registrationDate"] = _source_column(raw, "등기일자").reindex(df.index)
        df["id"] = df["source_row"].map(lambda row: f"csv:{digest}:{row}")
        df["updated_at"] = None  # file mtime is not a disclosure/update timestamp
        # Do not let the old importer's absent-column defaults invent evidence.
        if "거래유형" not in raw:
            df["dealType"] = "미상"
        df["dealType"] = df["dealType"].mask(_absent(df["dealType"]), "미상")
        if "층" not in raw:
            df["floor"] = np.nan
        else:
            df["floor"] = pd.to_numeric(raw["층"], errors="coerce").reindex(df.index)
        floor_valid = np.isfinite(df["floor"]) & df["floor"].gt(0) & df["floor"].mod(1).eq(0)
        audit["invalid_floor_rows"] += int((~floor_valid).sum())
        df.loc[~floor_valid, "floor"] = np.nan
        df["cancelled"] = ~_absent(df["cdealType"]) | ~_absent(df["cdealDay"])
        frames.append(df)

    source = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=DB_COLUMNS + SOURCE_COLUMNS)
    audit["date_selected_rows"] = len(source)
    valid_price = (np.isfinite(source["dealAmount"].astype(float)) & source["dealAmount"].gt(0)
                   & source["dealAmount"].mod(1).eq(0))
    audit["invalid_price_rows"] = int((~valid_price).sum())
    source = source[valid_price].copy()
    grouped = source.groupby(KEY, dropna=False, sort=False)
    source["has_active_cancelled_key"] = grouped["cancelled"].transform("nunique").gt(1)
    source["duplicate_count"] = grouped["id"].transform("size")
    audit["cancelled_rows"] = int(source["cancelled"].sum())
    audit["source_key_conflicts"] = len(source[source["has_active_cancelled_key"]].drop_duplicates(KEY))
    active = source[~source["cancelled"]].copy()
    active["duplicate_count"] = active.groupby(KEY, dropna=False)["id"].transform("size")
    audit["active_duplicate_groups"] = len(active[active["duplicate_count"].gt(1)].drop_duplicates(KEY))
    audit["active_duplicate_rows_removed"] = int(active.duplicated(KEY).sum())
    # Distinct nonempty metadata may mean distinct units; do not silently pick one.
    metadata_counts = active.groupby(KEY, dropna=False)[["building", "registrationDate", "dealType"]].nunique()
    if metadata_counts.gt(1).any(axis=1).any():
        raise ValueError("유효 CSV 행의 같은 자연키에 서로 다른 동/등기일/거래유형이 있습니다. 원본 거래 식별 확인이 필요합니다.")
    records = source[source["cancelled"] | source["has_active_cancelled_key"]].copy()
    records["conflict_type"] = np.where(records["has_active_cancelled_key"], "active_and_cancelled_same_key", "cancelled_source_row")
    active_duplicates = active[active["duplicate_count"].gt(1)].copy()
    active_duplicates["conflict_type"] = "active_duplicate_key"
    conflicts = pd.concat([records, active_duplicates], ignore_index=True).drop_duplicates("id")
    active["_metadata_count"] = active[["building", "registrationDate"]].notna().sum(axis=1)
    active = active.sort_values(["_metadata_count", "source_file", "source_row"]).drop_duplicates(KEY, keep="last")
    active = active.drop(columns=["_metadata_count", "has_active_cancelled_key"])
    active["cdealType"] = None
    active["cdealDay"] = None
    active = active.sort_values(KEY, na_position="last").reset_index(drop=True)
    audit["clean_rows"] = len(active)
    audit["active_rows_by_complex"] = {name: int(active["aptNm"].eq(name).sum()) for name in NAMES}
    audit["cancelled_rows_by_complex"] = {name: int((source["aptNm"].eq(name) & source["cancelled"]).sum()) for name in NAMES}
    audit["conflict_rows"] = len(conflicts)
    return active, audit, conflicts
