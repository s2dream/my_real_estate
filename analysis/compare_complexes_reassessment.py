#!/usr/bin/env python3
"""두 단지 84㎡ 실거래 재분석. 원본 DB/기존 분석은 수정하지 않는다.

실행: conda run -n py312 python analysis/compare_complexes_reassessment.py --as-of 2026-09-06
의존성: analysis/requirements.txt (차트가 필요 없으면 --no-charts)

주 추정량은 양쪽 거래가 있는 월×층 구간 안의 평균가격 차이를
n_SK*n_IP/(n_SK+n_IP)로 가중한 값이다. 이는 price ~ is_sk + C(cell)의
OLS 계수와 같다. 개별 주택의 적정가격, 입지의 인과효과, 매수 신호는 아니다.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from patsy import PatsyError


ROOT = Path(__file__).resolve().parents[1]
SK = "매교역푸르지오SKVIEW"
IP = "수원센트럴아이파크자이"
NAMES = (SK, IP)
KEY = ["dealDate", "sggCd", "umdNm", "jibun", "aptNm", "floor", "excluUseAr", "dealAmount"]
EMPTY = {"", "none", "null", "nan", "-"}
TIERS = ["1–5층", "6–15층", "16층 이상"]


def absent(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype(str).str.strip().str.lower().isin(EMPTY)


def read_data(db_path: Path) -> pd.DataFrame:
    """Parameter binding and read-only SQLite; no schema or import mutations."""
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        return pd.read_sql_query(
            "SELECT * FROM transactions WHERE aptNm IN (?, ?) ORDER BY id", conn, params=NAMES
        )


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["is_sk"] = (df["aptNm"] == SK).astype(int)
    df["price"] = df["dealAmount"] / 10000.0
    df["month"] = df["dealDate"].dt.strftime("%Y-%m")
    df["floor_tier"] = pd.cut(df["floor"], [0, 5, 15, np.inf], labels=TIERS).astype("object")
    df["cell"] = df["month"] + " / " + df["floor_tier"].fillna("층 미상")
    df["days"] = (df["dealDate"] - df["dealDate"].min()).dt.days
    return df


def prepare_data(raw: pd.DataFrame, start: pd.Timestamp, as_of: pd.Timestamp):
    """Keep an exact legacy sample, plus an audited normalized sample.

    Equal public keys are candidate duplicate observations, not proof of equal units.
    Prefer recent/nonmissing metadata; any cancellation in a group excludes that key.
    Grouping is done BEFORE cancellation filtering to avoid reviving stale records.
    """
    df = raw.copy()
    for col in ["cdealType", "cdealDay", "dealType", "updated_at", "sggCd", "umdNm", "jibun"]:
        if col not in df:
            df[col] = None
    for col in ["sggCd", "umdNm", "jibun"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["dealDate"] = pd.to_datetime(df["dealDate"], errors="coerce")
    for col in ["floor", "excluUseAr", "dealAmount"]:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    selected = df["dealDate"].between(start, as_of)
    legacy = df[selected & df["areaType"].eq("84타입") & (
        df["cdealType"].isna() | df["cdealType"].isin(["", "None"])
    )].copy()
    legacy["floor"] = legacy["floor"].fillna(0)
    audit = {
        "input_rows": len(df), "invalid_date_rows": int(df["dealDate"].isna().sum()),
        "outside_date_rows": int((df["dealDate"].notna() & ~selected).sum()),
    }
    df = df[selected].copy()
    audit["date_selected_rows"] = len(df)
    area_ok = df["excluUseAr"].ge(84) & df["excluUseAr"].lt(85)
    audit["outside_or_invalid_area_rows"] = int((~area_ok).sum())
    df = df[area_ok].copy()
    price_ok = np.isfinite(df["dealAmount"]) & df["dealAmount"].gt(0)
    audit["invalid_price_rows"] = int((~price_ok).sum())
    df = df[price_ok].copy()
    valid_floor = np.isfinite(df["floor"]) & df["floor"].gt(0) & df["floor"].mod(1).eq(0)
    audit["invalid_floor_rows"] = int((~valid_floor).sum())
    df.loc[~valid_floor, "floor"] = np.nan  # descriptive statistics retain these rows
    df["cancelled"] = ~absent(df["cdealType"]) | ~absent(df["cdealDay"])
    df["dealType"] = df["dealType"].mask(absent(df["dealType"]), "미상").astype(str).str.strip()
    grouped = df.groupby(KEY, dropna=False, sort=False)
    df["duplicate_count"] = grouped["id"].transform("size")
    df["group_cancelled"] = grouped["cancelled"].transform("any")
    def resolve_type(series):
        values = sorted(set(series) - {"미상"})
        return values[0] if len(values) == 1 else ("충돌" if values else "미상")
    df["_resolved_type"] = grouped["dealType"].transform(resolve_type)
    # Every removed source row remains inspectable in duplicate_candidates.csv.
    duplicates = df[df["duplicate_count"] > 1].copy()
    df["_updated"] = pd.to_datetime(df["updated_at"], errors="coerce", utc=True)
    df["_known_type"] = df["dealType"].ne("미상")
    ordered = df.sort_values(["_updated", "_known_type", "id"], na_position="first")
    canonical = ordered.drop_duplicates(KEY, keep="last").copy()
    # Do not lose a known trade type if the most recent snapshot omits it.
    canonical["dealType"] = canonical["_resolved_type"]
    audit["duplicate_groups"] = int((canonical["duplicate_count"] > 1).sum())
    audit["duplicate_rows_removed"] = len(df) - len(canonical)
    audit["cancelled_keys_removed"] = int(canonical["group_cancelled"].sum())
    audit["trade_type_conflict_keys"] = int(canonical["dealType"].eq("충돌").sum())
    cleaned = canonical[~canonical["group_cancelled"]].drop(columns=["_updated", "_known_type", "_resolved_type"])
    audit["clean_rows"] = len(cleaned)
    cleaned = cleaned.sort_values(KEY, na_position="last").reset_index(drop=True)
    return add_features(legacy), add_features(cleaned), duplicates, audit


def month_table(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Full calendar, including zero-trade months; never fill absent prices."""
    table = pd.DataFrame(index=pd.period_range(start, end, freq="M").astype(str))
    for name, prefix in [(SK, "sk"), (IP, "ip")]:
        grouped = df[df["aptNm"] == name].groupby("month")["price"]
        table[prefix + "_n"] = grouped.size().reindex(table.index, fill_value=0)
        table[prefix + "_median"] = grouped.median().reindex(table.index)
        table[prefix + "_mean"] = grouped.mean().reindex(table.index)
    table["gap_median"] = table["sk_median"] - table["ip_median"]
    table["gap_mean"] = table["sk_mean"] - table["ip_mean"]
    table.index.name = "month"
    return table.reset_index()


def comparable_sample(df: pd.DataFrame, cutoff: pd.Timestamp, min_per_month: int = 1) -> pd.DataFrame:
    """Common months and overlapping floor range; do not infer a missing price."""
    sub = df[(df["dealDate"] <= cutoff) & df["floor"].notna()].copy()
    if sub["aptNm"].nunique() < 2:
        return sub.iloc[:0]
    floors = sub.groupby("aptNm")["floor"].agg(["min", "max"])
    sub = sub[sub["floor"].between(floors["min"].max(), floors["max"].min())]
    counts = sub.groupby(["month", "aptNm"]).size().unstack(fill_value=0).reindex(columns=NAMES, fill_value=0)
    return sub[sub["month"].isin(counts.index[counts.min(axis=1) >= min_per_month])].copy()


def matched_cells(df: pd.DataFrame) -> pd.DataFrame:
    counts = df.groupby(["cell", "aptNm"]).size().unstack(fill_value=0).reindex(columns=NAMES, fill_value=0)
    return df[df["cell"].isin(counts.index[counts.min(axis=1) > 0])].copy()


def cell_table(df: pd.DataFrame) -> pd.DataFrame:
    columns = ["cell", "month", "floor_tier", "sk_n", "ip_n", "sk_mean", "ip_mean", "gap", "weight"]
    rows = []
    for cell, group in df.groupby("cell", sort=True):
        a, b = (group[group["aptNm"] == name]["price"] for name in NAMES)
        if len(a) and len(b):
            rows.append([cell, group["month"].iloc[0], group["floor_tier"].iloc[0],
                         len(a), len(b), a.mean(), b.mean(), a.mean() - b.mean(),
                         len(a) * len(b) / (len(a) + len(b))])
    return pd.DataFrame(rows, columns=columns)


def fit_gap(df: pd.DataFrame, label: str, formula: str, covariance: str = "cluster") -> dict:
    """Return explicit unavailability on insufficient overlap/rank, not fake precision."""
    result = {"label": label, "formula": formula, "covariance": covariance,
              "n": len(df), "sk_n": int(df["is_sk"].sum()),
              "ip_n": int((df["is_sk"] == 0).sum()), "months": df["month"].nunique(),
              "unit": "%" if formula.startswith("np.log") else "억원", "status": "ok"}
    if df["is_sk"].nunique() != 2:
        return {**result, "status": "산출 불가: 양쪽 거래 필요"}
    try:
        model = smf.ols(formula, data=df, missing="raise")
    except (ValueError, PatsyError):
        return {**result, "status": "산출 불가: 모형 입력에 결측/오류"}
    if not np.isfinite(model.endog).all() or not np.isfinite(model.exog).all():
        return {**result, "status": "산출 불가: 모형 입력에 비유한 값"}
    n, p = model.exog.shape
    if np.linalg.matrix_rank(model.exog) < p or n <= p + 5:
        return {**result, "status": "산출 불가: 공선성 또는 잔차 표본 부족"}
    base = model.fit()
    if not np.isfinite(base.params["is_sk"]):
        return {**result, "status": "산출 불가: 계수 불안정"}
    result.update(estimate=float(base.params["is_sk"]),
                  r_squared=float(base.rsquared) if np.isfinite(base.rsquared) else None, parameters=p)
    if covariance == "cluster":
        if result["months"] < 6:
            if result["unit"] == "%":
                result["estimate"] = float(np.expm1(result["estimate"]) * 100)
            return {**result, "status": "점추정만: 월 군집 6개 미만"}
        fit = model.fit(cov_type="cluster", cov_kwds={
            "groups": df["month"].to_numpy(), "use_correction": True, "df_correction": True,
        }, use_t=True)
    else:
        fit = model.fit(cov_type=covariance, use_t=True)
    bounds = fit.conf_int().loc["is_sk"]
    if np.isfinite(bounds).all():
        pvalue = float(fit.pvalues["is_sk"])
        result.update(ci_low=float(bounds.iloc[0]), ci_high=float(bounds.iloc[1]),
                      p_value=pvalue if np.isfinite(pvalue) else None)
    else:
        result["status"] = "점추정만: 분산 추정 불안정"
    if result["unit"] == "%":
        for key in ["estimate", "ci_low", "ci_high"]:
            if key in result:
                result[key] = float(np.expm1(result[key]) * 100)
    return result


def analyze(legacy, cleaned, start, as_of, lag_days=30, db_cleaned=None):
    # Include only calendar months whose last day precedes the analyst's buffer.
    buffer_date = as_of - pd.Timedelta(days=lag_days)
    end_of_month = buffer_date + pd.offsets.MonthEnd(0)
    cutoff = end_of_month if end_of_month <= buffer_date else buffer_date.to_period("M").start_time - pd.Timedelta(days=1)
    common = comparable_sample(cleaned, cutoff)
    matched = matched_cells(common)
    cells = cell_table(matched)
    primary_formula = "price ~ is_sk + C(cell)"
    db_sample = cleaned if db_cleaned is None else db_cleaned
    models = [
        fit_gap(legacy, "기존 방식 재현: 중복 포함·선형 시간/층", "price ~ is_sk + floor + days", "nonrobust"),
        fit_gap(db_sample.dropna(subset=["floor"]), "DB 중복 정리 후 기존 식", "price ~ is_sk + floor + days", "HC3"),
        fit_gap(common, "공통월·월/층구간 가산 보정", "price ~ is_sk + C(month) + C(floor_tier)"),
        fit_gap(matched, "주 분석: 공통 월×층구간", primary_formula),
        fit_gap(matched, "주 분석의 HC3 구간", primary_formula, "HC3"),
        fit_gap(matched_cells(comparable_sample(cleaned, as_of)), "최근 잠정월 포함", primary_formula),
    ]
    variants = [
        ("직거래 제외", cleaned[cleaned["dealType"] != "직거래"], 1),
        ("중개거래만", cleaned[cleaned["dealType"] == "중개거래"], 1),
        ("월별 각 3건 이상", cleaned, 3),
        ("월별 각 5건 이상", cleaned, 5),
    ]
    for label, data, min_n in variants:
        models.append(fit_gap(matched_cells(comparable_sample(data, cutoff, min_n)), label, primary_formula))
    trimmed = common.copy()
    bounds = trimmed.groupby("aptNm")["price"].transform(lambda s: s.quantile(0.01))
    upper = trimmed.groupby("aptNm")["price"].transform(lambda s: s.quantile(0.99))
    trimmed = trimmed[trimmed["price"].between(bounds, upper)]
    models.extend([
        fit_gap(matched_cells(trimmed), "단지별 양끝 1% 제외", primary_formula),
        fit_gap(common, "월 보정·층수 이차식", "price ~ is_sk + C(month) + floor + I(floor ** 2)"),
        fit_gap(common, "월 보정·개별 층 범주", "price ~ is_sk + C(month) + C(floor)"),
        fit_gap(matched, "로그가격: 같은 월×층구간", "np.log(price) ~ is_sk + C(cell)"),
    ])
    if db_cleaned is not None:
        models.append(fit_gap(matched_cells(comparable_sample(db_cleaned, cutoff)),
                              "DB 정리본으로 주 분석 재계산", primary_formula))
    # Sensitivity to a single observed month; this range is NOT a confidence interval.
    leave_one = []
    for month in sorted(matched["month"].unique()):
        row = fit_gap(matched[matched["month"] != month], month, primary_formula, "HC3")
        leave_one.append({"omitted_month": month, "estimate": row.get("estimate"), "status": row["status"]})
    monthly = month_table(cleaned, start, as_of)
    monthly["provisional"] = pd.to_datetime(monthly["month"]) + pd.offsets.MonthEnd(0) > cutoff
    return {"cutoff": cutoff, "common": common, "matched": matched, "cells": cells,
            "models": models, "monthly": monthly, "leave_one_month_out": leave_one}


def fmt(value, digits=3):
    return "—" if value is None or pd.isna(value) else f"{value:,.{digits}f}"


def markdown_table(headers, rows):
    def escape(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(map(escape, headers)) + " |",
                      "| " + " | ".join(["---"] * len(headers)) + " |"] +
                     ["| " + " | ".join(map(escape, row)) + " |" for row in rows])


def summary_rows(samples):
    rows = []
    for label, data in samples:
        a, b = (data[data["aptNm"] == name]["price"] for name in NAMES)
        rows.append([label, len(a), len(b), fmt(a.mean()), fmt(b.mean()),
                     fmt(a.mean() - b.mean()), fmt(a.median() - b.median())])
    return rows


def render_report(legacy, clean, duplicates, audit, result, metadata, charts, db_cleaned=None):
    monthly, cells = result["monthly"], result["cells"]
    paired = monthly[(monthly["sk_n"] > 0) & (monthly["ip_n"] > 0)]
    primary = result["models"][3]
    n_months = int(primary["months"])
    if "estimate" not in primary:
        conclusion = "비교 가능한 표본이 부족하여 주 분석의 보정 가격 차이를 산출하지 않았습니다."
    else:
        interval = f"모형 기반 95% 구간 {fmt(primary.get('ci_low'))}~{fmt(primary.get('ci_high'))}억원"
        conclusion = f"주 분석의 SKVIEW−센트럴아이파크자이 평균가격 차이는 **{primary['estimate']:+.3f}억원**입니다({interval})."
        if primary.get("ci_low", -np.inf) > 0:
            conclusion += " 관측된 비교 표본에서 SKVIEW가 더 높은 가격에 거래됐다는 근거는 남습니다."
        elif primary.get("ci_high", np.inf) < 0:
            conclusion += " 관측된 비교 표본에서 센트럴아이파크자이가 더 높은 가격에 거래됐다는 근거가 있습니다."
        else:
            conclusion += " 방향을 단정할 만큼 정밀한 구간 추정은 확보되지 않았습니다."
    db_sample = clean if db_cleaned is None else db_cleaned
    rows = summary_rows([("기존 DB 표본 재현", legacy), ("DB 공개 거래키 중복 정리", db_sample),
                         ("주 분석 원자료: " + metadata.get("source", "db"), clean),
                         ("관측월/층 범위 공통·잠정월 제외", result["common"]),
                         ("주 분석에 포함된 월×층구간", result["matched"])])
    model_rows = [[m["label"], f"{m['sk_n']}/{m['ip_n']}", m["months"], fmt(m.get("estimate")),
                   f"{fmt(m.get('ci_low'))} ~ {fmt(m.get('ci_high'))}", m["unit"], m["covariance"], m["status"]]
                  for m in result["models"]]
    coverage = []
    for name in NAMES:
        sub = clean[clean["aptNm"] == name]
        coverage.append([name, len(sub), str(sub["dealDate"].min().date()) if len(sub) else "—",
                         str(sub["dealDate"].max().date()) if len(sub) else "—", sub["month"].nunique(),
                         ", ".join(str(x) for x in sorted(sub["excluUseAr"].unique())),
                         int(sub["dealType"].eq("직거래").sum()), int(sub["dealType"].isin(["미상", "충돌"]).sum())])
    month_rows = [[r.month, f"{r.sk_n}/{r.ip_n}", fmt(r.sk_median, 2), fmt(r.ip_median, 2),
                   fmt(r.gap_median, 2), "잠정" if r.provisional else ("희소" if min(r.sk_n, r.ip_n) < 3 else "")]
                  for r in monthly.itertuples()]
    leave = [r["estimate"] for r in result["leave_one_month_out"] if r["estimate"] is not None]
    leave_text = f"{min(leave):.3f}~{max(leave):.3f}억원" if leave else "산출 불가"
    weighted = np.average(cells["gap"], weights=cells["weight"]) if len(cells) else np.nan
    support = f"{len(result['common'])}건 중 {len(result['matched'])}건, {len(cells)}개 월×층구간, {n_months}개월"
    graph = "\n\n".join(f"![{title}](charts/{file})" for title, file in charts)
    area_note = "전용면적/세부 평면은 보정하지 않았습니다. 면적 구성이 변하면 추가 모형 점검이 필요합니다."
    if clean.groupby("aptNm")["excluUseAr"].nunique().eq(1).all():
        area_note = "단지별 전용면적이 한 값뿐이라 단지 지표와 겹칩니다. 면적 계수를 별도로 식별할 수 없어 회귀에 넣지 않았습니다."
    source_note = "주 분석은 DB 정리본을 사용합니다. 원본 CSV의 해제/유효 재신고 상태는 복원하지 않은 결과입니다."
    if metadata.get("source") == "csv":
        source_note = (
            f"**주 분석은 원본 CSV 스냅샷의 유효 행 {len(clean)}건을 사용합니다.** "
            f"CSV 유효 키 중 DB 정리본에 없는 것은 {audit['csv_not_in_db_keys']}건, "
            f"DB 정리본에만 있는 것은 {audit['db_not_in_csv_keys']}건입니다. "
            "CSV에는 같은 공개 키의 해제 행과 유효 행이 함께 있을 수 있습니다. "
            "이를 단일 계약으로 합쳐 해제 우선 처리하지 않고, 원본에서 해제 행을 먼저 제외합니다. "
            "동/등기일/원본 행 번호를 보존하며 남은 유효 키의 반복만 중복 후보로 정리합니다. "
            "[CSV 상태 감사](csv_status_audit.csv), [CSV–DB 차이](csv_db_difference.csv), "
            "`results.json`의 파일별 해시를 통해 확인할 수 있습니다. "
            "기존 DB에는 없는 동 번호를 확보했지만 단지마다 서로 다른 동이므로 같은 주택 조건을 보장하지 않습니다."
        )
    return f"""# 매교역푸르지오SKVIEW vs 수원센트럴아이파크자이: 84㎡ 실거래 재분석

{conclusion}

다만 기존 보고서의 순수 입지 프리미엄, 평균회귀, 최적 매수 시점, 환금성 우월 주장은 이 데이터와 분석으로 입증되지 않습니다. **높게 거래됐다는 관찰과 더 좋은 투자라는 판단은 별개의 주장입니다.**

## 1. 범위와 데이터 감사

- 계약일 범위: {metadata['start_date']}~{metadata['as_of']}; 전용면적 84.0㎡ 이상 85.0㎡ 미만.
- 현재 로컬 DB/CSV 스냅샷을 분석했습니다. `--as-of`는 계약일 상한이며 당시 신고 상태를 복원하는 옵션은 아닙니다.
- 최근 {metadata['lag_days']}일을 관측 지연 완충기간으로 두고, 그 이전에 끝난 달만 주 분석에 사용: **{result['cutoff'].date()}까지**. 30일 기본값은 분석상 선택이며 신고 완료를 보증하지 않습니다. 잠정월 포함 결과도 아래에 제시했습니다.
- DB 감사: 날짜 필터 후 {audit['date_selected_rows']}행; 날짜 오류 {audit['invalid_date_rows']}행, 면적 제외 {audit['outside_or_invalid_area_rows']}행, 가격 오류 {audit['invalid_price_rows']}행, 층수 오류 {audit['invalid_floor_rows']}행.
- DB의 동일 공개 거래키 **{audit['duplicate_groups']}개 그룹에서 중복 {audit['duplicate_rows_removed']}행**을 합쳤고, 취소/해제 정보가 있는 {audit['cancelled_keys_removed']}개 키를 제외하여 DB 정리본 {audit['clean_rows']}건을 남겼습니다.
- 키: 계약일·지역코드·법정동·지번·단지·층·전용면적·금액. 연/월/일 문자열의 0 채움 차이를 제거합니다. 최근 메타데이터와 알려진 거래유형을 사용하고 어느 중복행에든 취소 정보가 있으면 해당 키를 제외합니다.
- DB에 동·호/계약 식별자가 없어 동일 조건의 서로 다른 실제 거래를 구분할 수 없습니다. 중복 후보 원행은 [duplicate_candidates.csv](duplicate_candidates.csv)에 보존하고, 중복을 유지한 결과도 비교표에 남겼습니다. 기존 DB의 UNIQUE/덮어쓰기 과정에서 사라진 행은 DB 정리만으로 복구할 수 없습니다.

{source_note}

{markdown_table(['단지', '건수', '첫 관측 계약일', '마지막 관측 계약일', '관측월 수', '실제 면적(㎡)', '직거래', '유형 미상/충돌'], coverage)}

관측 0건은 선택한 원자료에 유효 거래가 없다는 뜻이며 실제 시장의 거래 부재나 수집 완전성을 보장하지 않습니다. 두 단지의 공통 거래월은 {len(paired)}개월이고, 그중 한쪽이 3건 미만인 달은 {int((paired[['sk_n', 'ip_n']].min(axis=1) < 3).sum())}개월입니다. 양쪽 각 5건 이상인 달은 전체 범위에서 {int((paired[['sk_n', 'ip_n']].min(axis=1) >= 5).sum())}개월입니다.

## 2. 기존 숫자와 비교

금액 단위는 억원, 차이는 항상 SKVIEW−센트럴아이파크자이입니다. 각 행은 표본이 달라지므로 차이의 변화를 모두 보정 효과로 해석하면 안 됩니다. 기존 표본은 현재 DB에서 기존 필터와 회귀식을 재현한 값이며 과거 보고서를 파싱한 값은 아닙니다.

{markdown_table(['표본', 'SK 건수', '센트럴 건수', 'SK 평균', '센트럴 평균', '평균 차이', '전체 중위 차이'], rows)}

전체 거래의 중위 차이와 월별 중위 차이의 평균은 다른 통계입니다. 중복 정리 후 공통 관측월을 동일 가중한 월별 중위 차이의 평균은 **{fmt(paired['gap_median'].mean())}억원**입니다. 월별 최소·최대는 관측 범위일 뿐 안정적 밴드나 미래 경계가 아닙니다.

## 3. 월과 층 구성이 비교 가능한 거래

주 분석은 잠정월을 제외하고 양쪽에 거래가 있는 월, 양쪽의 관측 층 범위가 겹치는 범위를 사용합니다. 그 안에서도 같은 월×층구간에 양쪽 거래가 있을 때만 비교합니다. 층구간은 1~5층, 6~15층, 16층 이상입니다. 포함 범위는 **{support}**입니다. 제외된 구간의 주택으로 결과를 일반화할 수 없습니다.

각 구간의 평균 차이를 `n_SK × n_센트럴 / (n_SK + n_센트럴)`로 가중했습니다. 표본이 한쪽에 치우친 구간의 영향은 작아집니다. 직접 집계 값은 **{fmt(weighted)}억원**이며 `가격 ~ 단지 + C(월×층구간)` 회귀 계수와 일치해야 합니다. 이는 같은 구간의 관측 거래 차이이며 같은 동·향·상태의 주택 비교는 아닙니다.

95% 구간은 월별 오차 의존을 허용한 군집 표준오차, 소표본 보정, 월 수−1 자유도의 t 분포로 계산했습니다. 월이 6개 미만이면 군집 구간을 생략합니다. 이 기준도 분석상 최소 조건일 뿐 충분한 정밀도를 보장하지 않습니다. 월 간 자기상관, 거래 선택 편향, 누락 변수, 중복 판정 오류는 이 구간에 반영되지 않습니다. HC3 구간은 거래별 독립성을 더 강하게 가정한 비교용입니다. 구현 기준은 [statsmodels 공식 문서](https://www.statsmodels.org/dev/generated/statsmodels.regression.linear_model.OLSResults.get_robustcov_results.html)를 따릅니다.

## 4. 모형과 표본을 바꿨을 때

{markdown_table(['분석', 'SK/센트럴 건수', '월 수', '추정 차이', '95% 구간', '단위', '오차 처리', '상태'], model_rows)}

- 주 분석에서 한 달씩 제외한 추정 범위: **{leave_text}**. 민감도 범위이며 신뢰구간이 아닙니다.
- 직거래는 주 분석에 포함하고 별도 제외 결과를 제공합니다. 낮은 가격만으로 비정상 거래라고 판정하지 않습니다. 1% 절삭은 단지별 공통 표본의 분위수 기준이며 민감도 확인용입니다.
- 각 3/5건 기준은 겹치는 층 범위로 제한한 뒤, 월×층구간 매칭 전에 적용합니다. 매칭 후 실제 비교 거래 수는 더 작을 수 있습니다.
- 로그 모형은 `100 × (exp(계수)−1)`인 조건부 기하평균 가격 차이(%)입니다. 산술평균 차이나 개별 매물 수익률이 아닙니다.
- {area_note} 세부 평면·향·동·조망·수리상태·입주권/분양권 이력·매도 사유도 보정하지 못합니다.
- 모형 간 차이는 함수 형태와 비교 표본의 변화에 대한 민감도입니다. 결과 중 유리한 값만 선택해서는 안 됩니다.

## 5. 기존 해석에 대한 판단

| 기존 주장 | 재분석 판단 | 추가로 필요한 근거 |
| --- | --- | --- |
| 순수 브랜드/입지 프리미엄이 고착 | 가격 차이는 추정 가능하나 원인 분리는 불가 | 동·향·상태 등 주택 특성과 인과 식별 설계 |
| p값으로 99.9% 가격 서열 입증 | p값은 서열의 확률이나 인과 증명이 아님 | 효과 크기, 가정, 불확실성의 공동 검토 |
| 0.3~1.6억 안정 밴드·평균회귀 | 최소·최대 집계만으로 확인 불가 | 충분히 긴 공통 시계열, 안정성/회귀 검정과 외부 기간 검증 |
| SK가 1~2개월 선도 | 기존 코드에 시차 검정이 없음 | 시장 공통 요인 제거, 시차 모형과 표본 밖 검증 |
| 중층보다 고층 프리미엄 극대화 | 다른 단지와의 구간별 격차만으로 층 선택 효과를 알 수 없음 | 단지×층 상호작용과 신뢰구간, 동일 조건 비교 |
| 거래량이 많아 환금성 우월 | 원시 건수에 중복/관측기간 차이가 있으며 거래건수만으로 판정 불가 | 84㎡ 세대수, 매물 수, 매각기간, 매도 할인율 |
| 0.3억 이내 갈아타기·0.8~1억 이상 과열 | 기준을 검증한 백테스트가 없어 매수 규칙으로 채택 불가 | 거래비용과 자금조건을 포함한 기간 외 성과 검증 |

p값을 가설이 참일 확률이나 효과의 중요도로 해석할 수 없다는 기준은 [미국통계학회 성명](https://www.amstat.org/asa/files/pdfs/p-valuestatement.pdf)에 근거합니다. 위 투자·인과 주장은 검증되지 않았다는 판단이며 반대 주장이 입증됐다는 뜻도 아닙니다.

## 6. 월별 관측값

희소 표시는 한쪽 3건 미만, 잠정 표시는 관측 완충기간 때문에 주 분석에서 제외한 월입니다. 거래가 없는 달의 가격/격차는 결측으로 남깁니다. 잠정월의 최근 가격만 보고 수렴·발산을 확정하지 않습니다.

{markdown_table(['월', 'SK/센트럴 건수', 'SK 중위(억)', '센트럴 중위(억)', '중위 차이(억)', '주의'], month_rows)}

{graph}

## 7. 재현과 산출물

```bash
conda run -n py312 python analysis/compare_complexes_reassessment.py --as-of {metadata['as_of']} --start-date {metadata['start_date']} --lag-days {metadata['lag_days']} --source {metadata.get('source', 'db')}
```

- [정리된 거래](cleaned_transactions.csv), [실제 주 분석 거래](matched_transactions.csv), [월별 집계](monthly.csv), [월×층구간 집계/가중치](matched_cells.csv)
- [모형별 수치](model_comparison.csv), [한 달씩 제외한 결과](leave_one_month_out.csv), [감사·버전·설정·해시](results.json)
- 입력 DB SHA-256: `{metadata['database_sha256']}`
- 조회 원행 SHA-256: `{metadata['query_rows_sha256']}`
- 실행 Python: `{metadata['python']}`

원본 `analysis/compare_complexes.py`, 기존 보고서와 DB 내용은 변경하지 않습니다. 이 결과는 저장된 실거래 표본의 비교이며 현재 호가나 향후 가격 전망을 추정하지 않습니다.
"""


def make_charts(result, output):
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "real_estate_mpl"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib import font_manager
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ["AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Malgun Gothic"]:
        if name in available:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False
    chart_dir = output / "charts"
    chart_dir.mkdir(exist_ok=True)
    m = result["monthly"]
    dates = pd.to_datetime(m["month"])
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True, constrained_layout=True)
    for prefix, label, color in [("sk", "SKVIEW", "#2563eb"), ("ip", "Central Ipark Xi", "#c65b20")]:
        axes[0].plot(dates, m[prefix + "_median"], ".-", label=label, color=color)
        shift = pd.Timedelta(days=-5 if prefix == "sk" else 5)
        axes[2].bar(dates + shift, m[prefix + "_n"], width=9, color=color, label=label)
    axes[0].set(title="84㎡: monthly observations after duplicate reconciliation", ylabel="Median price (KRW 100m)")
    axes[0].legend()
    axes[1].plot(dates, m["gap_median"], ".-", color="#475569")
    scarce = (m[["sk_n", "ip_n"]].min(axis=1) < 3) & m["gap_median"].notna()
    axes[1].scatter(dates[scarce], m.loc[scarce, "gap_median"], facecolors="none", edgecolors="#d97706", s=85, label="Either complex: n < 3")
    axes[1].axhline(0, color="gray", linewidth=0.7)
    axes[1].set_ylabel("SK - Central median gap\n(KRW 100m)")
    axes[1].legend()
    axes[2].set_ylabel("Transactions")
    axes[2].xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    for ax in axes:
        ax.grid(alpha=0.2)
        ax.axvspan(result["cutoff"] + pd.Timedelta(days=1), dates.iloc[-1] + pd.offsets.MonthEnd(0), color="#ef4444", alpha=0.08)
    fig.savefig(chart_dir / "monthly_observations.png", dpi=160)
    plt.close(fig)
    estimates = [x for x in result["models"] if x["unit"] == "억원" and "ci_low" in x]
    if estimates:
        fig, ax = plt.subplots(figsize=(12, 7), constrained_layout=True)
        for i, row in enumerate(estimates):
            ax.plot([row["ci_low"], row["ci_high"]], [i, i], color="#64748b")
            ax.scatter(row["estimate"], i, color="#2563eb")
        ax.set_yticks(range(len(estimates)), [x["label"] for x in estimates])
        ax.invert_yaxis()
        ax.axvline(0, color="gray", linewidth=0.8)
        ax.set(xlabel="SK - Central gap (KRW 100m), model-based 95% intervals", title="Specification / sample sensitivity")
        ax.grid(axis="x", alpha=0.2)
        fig.savefig(chart_dir / "model_sensitivity.png", dpi=160)
        plt.close(fig)
    charts = [("월별 가격·격차·거래 수(붉은 영역: 잠정월)", "monthly_observations.png")]
    if estimates:
        charts.append(("모형·표본별 추정 차이와 구간", "model_sensitivity.png"))
    return charts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=ROOT / "data/transactions.db")
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--as-of", default=datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat())
    parser.add_argument("--lag-days", type=int, default=30)
    parser.add_argument("--source", choices=["csv", "db"], default="csv", help="주 분석 원자료 (기본: 원본 CSV)")
    parser.add_argument("--csv-dir", type=Path, default=ROOT / "data/csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "analysis/maegyo_skview_vs_central_ipark_reassessment")
    parser.add_argument("--no-charts", action="store_true")
    args = parser.parse_args(argv)
    try:
        start, as_of = (pd.Timestamp(datetime.strptime(v, "%Y-%m-%d")) for v in [args.start_date, args.as_of])
    except ValueError:
        parser.error("날짜는 YYYY-MM-DD 형식이어야 합니다.")
    if start > as_of or args.lag_days < 0:
        parser.error("시작일은 기준일 이하여야 하며 lag-days는 0 이상이어야 합니다.")
    if not args.db_path.is_file():
        parser.error(f"DB 파일을 찾을 수 없습니다: {args.db_path}")
    raw = read_data(args.db_path)
    legacy, db_clean, duplicates, audit = prepare_data(raw, start, as_of)
    clean = db_clean
    csv_audit = {}
    if args.source == "csv":
        if __package__:
            from .reassessment_csv import load_csv_snapshot
        else:
            from reassessment_csv import load_csv_snapshot
        try:
            csv_active, csv_audit, csv_status = load_csv_snapshot(args.csv_dir, start, as_of)
        except (ValueError, FileNotFoundError) as error:
            parser.error(str(error))
        clean = add_features(csv_active)
        difference = clean[KEY].merge(db_clean[KEY], on=KEY, how="outer", indicator=True)
        difference = difference[difference["_merge"] != "both"].copy()
        audit["csv_not_in_db_keys"] = int(difference["_merge"].eq("left_only").sum())
        audit["db_not_in_csv_keys"] = int(difference["_merge"].eq("right_only").sum())
    if clean["aptNm"].nunique() < 2:
        parser.error("정리 후 두 단지 모두에 거래가 있어야 합니다. 날짜/DB를 확인하세요.")
    result = analyze(legacy, clean, start, as_of, args.lag_days, db_cleaned=db_clean)
    output = args.output_dir
    # The reassessment must never overwrite the original generated report.
    if output.resolve() == (ROOT / "analysis/maegyo_skview_vs_central_ipark").resolve():
        parser.error("기존 보고서 폴더 대신 새 출력 폴더를 지정하세요.")
    output.mkdir(parents=True, exist_ok=True)
    if args.source == "csv":
        csv_status.to_csv(output / "csv_status_audit.csv", index=False, encoding="utf-8-sig")
        difference.to_csv(output / "csv_db_difference.csv", index=False, encoding="utf-8-sig")
    for name, frame in [("cleaned_transactions", clean), ("db_cleaned_transactions", db_clean), ("duplicate_candidates", duplicates),
                        ("matched_transactions", result["matched"]), ("monthly", result["monthly"]),
                        ("matched_cells", result["cells"]), ("model_comparison", pd.DataFrame(result["models"])),
                        ("leave_one_month_out", pd.DataFrame(result["leave_one_month_out"]))]:
        frame.to_csv(output / f"{name}.csv", index=False, encoding="utf-8-sig")
    metadata = {"start_date": args.start_date, "as_of": args.as_of, "lag_days": args.lag_days, "source": args.source,
                "csv_dir": str(args.csv_dir.resolve()), "csv_audit": csv_audit,
                "primary_cutoff": str(result["cutoff"].date()), "database": str(args.db_path.resolve()),
                "database_sha256": hashlib.sha256(args.db_path.read_bytes()).hexdigest(),
                "query_rows_sha256": hashlib.sha256(raw.to_json(orient="split", force_ascii=False).encode()).hexdigest(),
                "python": sys.version.split()[0], "executable": sys.executable,
                "versions": {name: importlib.metadata.version(name) for name in ["pandas", "numpy", "statsmodels"]}}
    document = {"metadata": metadata, "audit": audit, "models": result["models"],
                "leave_one_month_out": result["leave_one_month_out"]}
    (output / "results.json").write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    charts = [] if args.no_charts else make_charts(result, output)
    (output / "report.md").write_text(render_report(legacy, clean, duplicates, audit, result, metadata, charts, db_cleaned=db_clean), encoding="utf-8")
    print(f"보고서: {output / 'report.md'}")
    print(f"정리 후 거래: SK {int((clean.aptNm == SK).sum())}건 / 센트럴 {int((clean.aptNm == IP).sum())}건")
    print(json.dumps(result["models"][3], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
