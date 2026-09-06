#!/usr/bin/env python3
"""두 단지 84㎡ 실거래의 가격 분포, 거래 구성, 조건부 가격 차이 분석.

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


def analyze(cleaned, start, as_of, lag_days=30):
    # Include only calendar months whose last day precedes the analyst's buffer.
    buffer_date = as_of - pd.Timedelta(days=lag_days)
    end_of_month = buffer_date + pd.offsets.MonthEnd(0)
    cutoff = end_of_month if end_of_month <= buffer_date else buffer_date.to_period("M").start_time - pd.Timedelta(days=1)
    common = comparable_sample(cleaned, cutoff)
    matched = matched_cells(common)
    cells = cell_table(matched)
    primary_formula = "price ~ is_sk + C(cell)"
    models = [
        fit_gap(matched, "주 분석: 공통 월×층구간", primary_formula),
        fit_gap(common, "공통월·월/층구간 가산 보정", "price ~ is_sk + C(month) + C(floor_tier)"),
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
    # Sensitivity to a single observed month; this range is NOT a confidence interval.
    leave_one = []
    for month in sorted(matched["month"].unique()):
        row = fit_gap(matched[matched["month"] != month], month, primary_formula, "HC3")
        leave_one.append({"omitted_month": month, "estimate": row.get("estimate"), "status": row["status"]})
    monthly = month_table(cleaned, start, as_of)
    monthly["provisional"] = pd.to_datetime(monthly["month"]) + pd.offsets.MonthEnd(0) > cutoff
    return {"cutoff": cutoff, "common": common, "matched": matched, "cells": cells,
            "primary": models[0], "models": models, "monthly": monthly,
            "distributions": distribution_table(cleaned, common),
            "quarters": quarter_table(common), "floors": floor_table(common, cells),
            "matched_months": matched_month_table(cells),
            "leave_one_month_out": leave_one}


def distribution_table(cleaned, common):
    rows = []
    for scope, sample in [("전체 유효 거래", cleaned), ("공통월·공통 층 범위", common)]:
        for name in NAMES:
            sub = sample[sample["aptNm"] == name]
            prices = sub["price"]
            rows.append({"scope": scope, "aptNm": name, "n": len(sub),
                         "mean": prices.mean(), "median": prices.median(),
                         "q25": prices.quantile(0.25), "q75": prices.quantile(0.75),
                         "min": prices.min(), "max": prices.max()})
    return pd.DataFrame(rows)


def quarter_table(common):
    columns = ["quarter", "months", "sk_n", "ip_n", "sk_median", "ip_median", "gap_median"]
    rows = []
    for quarter, sub in common.groupby(common["dealDate"].dt.to_period("Q")):
        a, b = (sub[sub["aptNm"] == name]["price"] for name in NAMES)
        rows.append([str(quarter), ", ".join(sorted(sub["month"].unique())), len(a), len(b),
                     a.median(), b.median(), a.median() - b.median()])
    return pd.DataFrame(rows, columns=columns)


def floor_table(common, cells):
    rows = []
    totals = common["aptNm"].value_counts()
    for tier in TIERS:
        sub = common[common["floor_tier"] == tier]
        a, b = (sub[sub["aptNm"] == name]["price"] for name in NAMES)
        matched = cells[cells["floor_tier"] == tier]
        rows.append({"floor_tier": tier, "sk_n": len(a), "ip_n": len(b),
                     "sk_share_pct": len(a) / totals[SK] * 100 if totals.get(SK, 0) else np.nan,
                     "ip_share_pct": len(b) / totals[IP] * 100 if totals.get(IP, 0) else np.nan,
                     "sk_median": a.median(), "ip_median": b.median(),
                     "matched_sk_n": matched["sk_n"].sum(), "matched_ip_n": matched["ip_n"].sum(),
                     "matched_months": matched["month"].nunique(),
                     "matched_gap": np.average(matched["gap"], weights=matched["weight"]) if len(matched) else np.nan})
    return pd.DataFrame(rows)


def matched_month_table(cells):
    columns = ["month", "sk_n", "ip_n", "cells", "weighted_gap"]
    rows = []
    for month, sub in cells.groupby("month"):
        rows.append([month, sub["sk_n"].sum(), sub["ip_n"].sum(), len(sub),
                     np.average(sub["gap"], weights=sub["weight"])])
    return pd.DataFrame(rows, columns=columns)


def fmt(value, digits=3):
    return "—" if value is None or pd.isna(value) else f"{value:,.{digits}f}"


def markdown_table(headers, rows):
    def escape(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(map(escape, headers)) + " |",
                      "| " + " | ".join(["---"] * len(headers)) + " |"] +
                     ["| " + " | ".join(map(escape, row)) + " |" for row in rows])


def render_report(clean, audit, result, metadata, charts):
    """A standalone assessment; every numeric statement uses the selected data."""
    monthly, cells = result["monthly"], result["cells"]
    common, matched = result["common"], result["matched"]
    paired = monthly[(monthly["sk_n"] > 0) & (monthly["ip_n"] > 0)]
    primary = result["primary"]
    if "estimate" not in primary:
        conclusion = "비교 가능한 표본이 부족하여 월·층구간을 맞춘 가격 차이를 산출하지 않았습니다."
    else:
        interval = (f"모형 기반 95% 구간 {fmt(primary['ci_low'])}~{fmt(primary['ci_high'])}억원"
                    if "ci_low" in primary else "표본이 부족하여 95% 구간은 제시하지 않음")
        conclusion = f"같은 월·층구간에 양쪽 거래가 있는 표본의 SKVIEW−센트럴아이파크자이 평균가격 차이는 **{primary['estimate']:+.3f}억원**입니다({interval})."
        if primary.get("ci_low", -np.inf) > 0:
            conclusion += " 관측된 비교 표본에서 SKVIEW가 더 높은 가격에 거래됐다는 근거가 있습니다."
        elif primary.get("ci_high", np.inf) < 0:
            conclusion += " 관측된 비교 표본에서 센트럴아이파크자이가 더 높은 가격에 거래됐다는 근거가 있습니다."
        else:
            conclusion += " 차이의 방향을 단정할 만큼 정밀한 구간 추정은 확보되지 않았습니다."

    def chart(filename):
        return next((f"![{title}](charts/{file})" for title, file in charts if file == filename), "")

    coverage = []
    for name in NAMES:
        sub = clean[clean["aptNm"] == name]
        coverage.append([name, len(sub), str(sub["dealDate"].min().date()) if len(sub) else "—",
                         str(sub["dealDate"].max().date()) if len(sub) else "—", sub["month"].nunique(),
                         ", ".join(str(x) for x in sorted(sub["excluUseAr"].unique())),
                         int(sub["dealType"].eq("직거래").sum()), int(sub["dealType"].isin(["미상", "충돌"]).sum())])
    dist = result["distributions"]
    def distribution_rows(scope):
        return [[r.aptNm, r.n, fmt(r.mean), fmt(r.median), f"{fmt(r.q25)}~{fmt(r.q75)}",
                 f"{fmt(r.min)}~{fmt(r.max)}"] for r in dist[dist["scope"] == scope].itertuples()]

    a = common[common["aptNm"] == SK]["price"]
    b = common[common["aptNm"] == IP]["price"]
    distribution_note = "기간과 층 범위가 겹치는 거래가 부족하여 공통 표본의 가격 분포를 평가하기 어렵습니다."
    if len(a) and len(b):
        distribution_note = (
            f"공통 표본의 중위가격은 SKVIEW **{fmt(a.median())}억원**, 센트럴아이파크자이 **{fmt(b.median())}억원**으로, "
            f"차이는 {fmt(a.median() - b.median())}억원입니다. 평균 차이는 {fmt(a.mean() - b.mean())}억원입니다. "
        )
        if a.quantile(.25) > b.quantile(.75):
            distribution_note += "SKVIEW의 중앙 50% 가격 구간 하단이 센트럴아이파크자이의 중앙 50% 구간 상단보다 높아, 거래가 집중된 가격대에도 차이가 나타납니다. "
        elif b.quantile(.25) > a.quantile(.75):
            distribution_note += "센트럴아이파크자이의 중앙 50% 가격 구간 하단이 SKVIEW의 중앙 50% 구간 상단보다 높아, 거래가 집중된 가격대에도 차이가 나타납니다. "
        else:
            distribution_note += "두 단지의 중앙 50% 가격 구간은 서로 겹칩니다. "
        if max(a.min(), b.min()) <= min(a.max(), b.max()):
            distribution_note += "전체 최저~최고 범위는 겹치므로 개별 거래의 가격 순서가 항상 같다는 뜻은 아닙니다."
    quarter_rows = [[r.quarter, r.months, f"{r.sk_n}/{r.ip_n}", fmt(r.sk_median),
                     fmt(r.ip_median), fmt(r.gap_median)] for r in result["quarters"].itertuples()]
    recent_quarters = result["quarters"].tail(4)
    quarter_note = "공통 관측 분기가 부족하여 가격대의 이동을 평가하기 어렵습니다."
    if len(recent_quarters) >= 2:
        sk_path = " → ".join(fmt(v) for v in recent_quarters["sk_median"])
        ip_path = " → ".join(fmt(v) for v in recent_quarters["ip_median"])
        quarter_note = (f"최근 {len(recent_quarters)}개 분기 관측구간({recent_quarters.iloc[0]['quarter']}~"
                        f"{recent_quarters.iloc[-1]['quarter']})의 중위가격은 SKVIEW가 **{sk_path}억원**, "
                        f"센트럴아이파크자이가 **{ip_path}억원**입니다. ")
        if recent_quarters["sk_median"].diff().dropna().gt(0).all():
            quarter_note += "이 구간에서 SKVIEW의 관측 중위가격은 순차적으로 높아집니다. "
        elif recent_quarters["sk_median"].diff().dropna().lt(0).all():
            quarter_note += "이 구간에서 SKVIEW의 관측 중위가격은 순차적으로 낮아집니다. "
        quarter_note += "이는 거래된 주택의 가격 분포 변화이며 같은 주택을 반복 관측한 결과는 아닙니다."
    floor_rows = [[r.floor_tier, f"{r.sk_n} ({fmt(r.sk_share_pct, 1)}%)",
                   f"{r.ip_n} ({fmt(r.ip_share_pct, 1)}%)", fmt(r.sk_median), fmt(r.ip_median),
                   f"{r.matched_sk_n}/{r.matched_ip_n}", r.matched_months, fmt(r.matched_gap)]
                  for r in result["floors"].itertuples()]
    high = result["floors"].iloc[-1]
    floor_note = (f"공통 표본의 고층 거래 비중은 SKVIEW {fmt(high.sk_share_pct, 1)}%, "
                  f"센트럴아이파크자이 {fmt(high.ip_share_pct, 1)}%입니다. "
                  "거래된 층 구성이 다르므로 전체 평균만으로 단지 간 가격 차이를 평가하기에는 한계가 있습니다. "
                  "같은 층구간에도 실제 층·동·향·주택 상태의 차이가 남습니다.")

    # Describe the latest complete observed month and its matched observations,
    # explicitly retaining their different populations and estimands.
    complete = paired[~paired["provisional"]]
    latest_note = "잠정월을 제외하면 양쪽 가격을 함께 관측한 달이 없습니다."
    if len(complete):
        last = complete.iloc[-1]
        latest_note = (f"잠정월을 제외한 최근 공통 관측월 **{last['month']}**의 전 층 유효 거래는 "
                       f"SKVIEW {int(last['sk_n'])}건, 센트럴아이파크자이 {int(last['ip_n'])}건입니다. "
                       f"각 중위가격은 {fmt(last['sk_median'], 2)}억원과 {fmt(last['ip_median'], 2)}억원, "
                       f"차이는 **{fmt(last['gap_median'], 2)}억원**입니다. ")
        row = result["matched_months"][result["matched_months"]["month"] == last["month"]]
        if len(row):
            r = row.iloc[0]
            latest_note += (f"이 달에서 같은 층구간끼리 비교한 가중평균 차이는 **{fmt(r.weighted_gap)}억원**"
                            f"(SK {int(r.sk_n)}건/센트럴 {int(r.ip_n)}건)입니다. "
                            "표본과 통계량이 다르므로 두 수치의 차이 전체를 층수 보정 효과로 해석할 수 없습니다. "
                            "월 중위가격의 차이가 커졌다는 사실만으로 같은 조건의 주택 간 격차도 확대됐다고 판단하기 어렵습니다.")
    provisional = paired[paired["provisional"]]
    provisional_note = "기준일 부근에 양쪽 거래를 함께 관측한 잠정월은 없습니다."
    if len(provisional):
        p = provisional.iloc[-1]
        provisional_note = (f"잠정월 {p['month']}에는 SKVIEW {int(p.sk_n)}건·센트럴아이파크자이 {int(p.ip_n)}건이 있으며, "
                            f"중위가격 차이는 {fmt(p.gap_median, 2)}억원입니다. "
                            "추가 신고와 해제 반영으로 표본이 바뀔 수 있어 주 분석과 분리해 표시합니다.")

    extreme_rows = []
    extreme_notes = []
    for name in NAMES:
        sub = common[common["aptNm"] == name]
        if len(sub):
            row = sub.loc[sub["price"].idxmin()]
            extreme_rows.append([name, row["dealDate"].strftime("%Y-%m-%d"), int(row["floor"]),
                                 fmt(row["price"], 2), row["dealType"]])
            month_prices = sub.loc[sub["month"] == row["month"], "price"]
            short_name = "SKVIEW" if name == SK else "센트럴아이파크자이"
            extreme_notes.append(f"{short_name}의 해당 월({row['month']}) 공통 표본 {len(month_prices)}건의 "
                                 f"평균은 {fmt(month_prices.mean(), 2)}억원, 중위가격은 {fmt(month_prices.median(), 2)}억원입니다.")
    model_rows = [[m["label"], f"{m['sk_n']}/{m['ip_n']}", m["months"], fmt(m.get("estimate")),
                   f"{fmt(m.get('ci_low'))}~{fmt(m.get('ci_high'))}", m["unit"],
                   "산출" if m["status"] == "ok" else m["status"]] for m in result["models"]]
    estimates = [m["estimate"] for m in result["models"] if m["unit"] == "억원" and "estimate" in m]
    sensitivity_note = "표본/모형별 가격 차이를 충분히 산출하지 못했습니다."
    if estimates:
        sensitivity_note = f"계산 가능한 원화 모형들의 점추정은 {min(estimates):.3f}~{max(estimates):.3f}억원입니다. "
        if min(estimates) > 0:
            sensitivity_note += "검토한 조건에서는 SKVIEW가 더 높은 가격에 거래되는 방향이 유지됩니다."
        elif max(estimates) < 0:
            sensitivity_note += "검토한 조건에서는 센트럴아이파크자이가 더 높은 가격에 거래되는 방향이 유지됩니다."
        else:
            sensitivity_note += "선택한 표본이나 모형에 따라 가격 차이의 방향이 달라집니다."
        sensitivity_note += " 이 범위는 민감도 범위이며 신뢰구간이 아닙니다."
    leave = [r["estimate"] for r in result["leave_one_month_out"] if r["estimate"] is not None]
    leave_text = f"{min(leave):.3f}~{max(leave):.3f}억원" if leave else "산출 불가"
    sparse_cells = int((cells[["sk_n", "ip_n"]].min(axis=1) == 1).sum())
    well_sampled_cells = int((cells[["sk_n", "ip_n"]].min(axis=1) >= 3).sum())
    positive_cells = int(cells["gap"].gt(0).sum())
    negative_cells = int(cells["gap"].lt(0).sum())
    precision_note = ("세부 월·층구간의 작은 표본 때문에 개별 구간의 가격 차이 크기는 신중하게 해석해야 합니다."
                      if len(cells) else "비교 가능한 월·층구간이 없어 가격 차이에 대한 평가를 보류합니다.")
    area_note = "면적 범위를 제한했지만 세부 면적/평면 차이를 별도로 보정하지 않았습니다."
    if clean.groupby("aptNm")["excluUseAr"].nunique().eq(1).all():
        area_note = "단지마다 전용면적이 한 값뿐이므로 면적 효과와 단지 효과를 따로 식별할 수 없습니다."
    if metadata.get("source") == "csv":
        ca = metadata["csv_audit"]
        source_note = (f"국토교통부 실거래가 공개 자료의 CSV 스냅샷에서 대상 기간·단지·면적에 해당하는 "
                       f"{ca['date_selected_rows']}행을 확인했습니다. 해제 행 {ca['cancelled_rows']}건과 "
                       f"유효 행의 중복 후보 {ca['active_duplicate_rows_removed']}행을 제외하여 **{len(clean)}건**을 사용합니다. "
                       f"가격 오류로 제외한 행은 {ca['invalid_price_rows']}건입니다. "
                       "같은 공개 키에 해제 행과 유효 행이 함께 있는 경우 원본의 상태를 각각 보존하고 유효 행을 사용합니다.")
    else:
        source_note = (f"SQLite에 저장된 대상 거래에서 공개 거래키를 정규화하고 중복 {audit['duplicate_rows_removed']}행, "
                       f"취소/해제 키 {audit['cancelled_keys_removed']}건을 제외한 **{len(clean)}건**을 사용합니다. "
                       "중복행 중 어느 하나에 취소 정보가 있으면 해당 키를 제외합니다.")
    month_rows = [[r.month, f"{r.sk_n}/{r.ip_n}", fmt(r.sk_median, 2), fmt(r.ip_median, 2),
                   fmt(r.gap_median, 2), "잠정" if r.provisional else ("희소" if min(r.sk_n, r.ip_n) < 3 else "")]
                  for r in monthly.itertuples()]
    common_period = (f"{common['month'].min()}~{common['month'].max()}의 공통 관측월 {common['month'].nunique()}개월"
                     if len(common) else "공통 관측월 없음")
    floor_range = (f"{int(common['floor'].min())}~{int(common['floor'].max())}층" if len(common) else "층 범위 없음")

    return f"""# 매교역푸르지오SKVIEW·수원센트럴아이파크자이 84㎡ 실거래 분석

{conclusion}

평가의 핵심은 **거래가 집중된 가격대의 차이, 시기와 층 구성에 따른 변동, 관측 표본의 충분성**입니다. 가격 차이는 관측된 거래 특성을 반영한 값이며 개별 주택의 적정가격이나 향후 수익률을 뜻하지 않습니다.

## 1. 데이터 범위와 관측 여건

{source_note}

계약일 범위는 **{metadata['start_date']}~{metadata['as_of']}**, 전용면적은 84.0㎡ 이상 85.0㎡ 미만입니다. 현재 확보한 스냅샷의 계약일을 제한한 것으로, 과거 시점의 신고 상태를 복원한 자료는 아닙니다. 최근 {metadata['lag_days']}일의 관측 지연을 고려하여 완충기간 시작일까지 종료된 달, 즉 **{result['cutoff'].date()}까지**를 주 분석에 사용합니다. 완충기간은 신고 완료를 보장하지 않습니다.

{markdown_table(['단지', '유효 건수', '첫 관측 계약일', '마지막 관측 계약일', '관측월 수', '실제 면적(㎡)', '직거래', '유형 미상/충돌'], coverage)}

두 단지에 거래가 함께 있는 달은 전체 범위에서 {len(paired)}개월입니다. 그중 한쪽이 3건 미만인 달은 {int((paired[['sk_n', 'ip_n']].min(axis=1) < 3).sum())}개월, 양쪽 각 5건 이상인 달은 {int((paired[['sk_n', 'ip_n']].min(axis=1) >= 5).sum())}개월입니다. **거래 수가 적은 달의 중위가격은 소수 거래의 조건에 크게 좌우됩니다.** 관측 0건은 자료에 유효 거래가 없다는 뜻이며 실제 시장의 거래 부재나 수집 완전성을 보장하지 않습니다.

## 2. 가격 수준과 분포

두 단지 전체 유효 거래의 분포는 다음과 같습니다. 금액은 모두 억원이며, 중앙 50%는 25~75분위 구간입니다. 두 단지의 관측 기간과 월별 거래 비중이 달라 이 표만으로 같은 시점의 주택 가격을 비교할 수는 없습니다.

{markdown_table(['단지', '건수', '평균', '중위', '중앙 50%', '최저~최고'], distribution_rows('전체 유효 거래'))}

가격 수준을 비교할 때는 **{common_period}, {floor_range}**의 거래로 제한합니다. 공통 표본은 같은 관측월과 겹치는 층 범위를 사용하지만, 월·층별 거래 비중까지 같게 만든 표본은 아닙니다.

{markdown_table(['단지', '공통 표본 건수', '평균', '중위', '중앙 50%', '최저~최고'], distribution_rows('공통월·공통 층 범위'))}

{distribution_note}

{chart('price_distribution.png')}

공통 표본에서 가장 낮은 거래의 조건은 다음과 같습니다. 최저가 하나를 단지의 대표 가격으로 해석하기보다 거래유형과 층을 함께 확인할 필요가 있습니다.

{markdown_table(['단지', '계약일', '층', '가격(억)', '거래유형'], extreme_rows)}

{' '.join(extreme_notes)} 평균과 중위가격을 함께 보면 소수 저가 거래가 월별 대표가격에 미치는 영향을 확인할 수 있습니다.

낮은 가격만으로 오류나 특수관계인 거래라고 판단하지 않습니다. 직거래와 분포 양끝의 가격을 제외하는 민감도 분석을 별도로 제공합니다.

## 3. 시기별 가격과 거래 구성

아래 표는 공통 표본을 분기별로 집계한 것입니다. **표에 적힌 관측월만 포함**하므로 온전한 분기의 시장지수는 아닙니다. 거래 구성에 따라 중위가격이 달라질 수 있어 동일 주택의 가격 상승률로 해석하지 않습니다.

{markdown_table(['분기', '포함된 관측월', 'SK/센트럴 건수', 'SK 중위(억)', '센트럴 중위(억)', '중위 차이(억)'], quarter_rows)}

{quarter_note}

{latest_note}

{provisional_note}

{chart('monthly_observations.png')}

## 4. 층 구성과 같은 조건 구간의 가격 차이

{floor_note}

아래 중위가격과 비중은 공통 표본 기준입니다. 마지막 열은 양쪽 거래가 있는 월×층구간만 남겨 구간별 평균 차이를 가중한 값입니다. 층구간마다 포함되는 월과 거래가 달라 마지막 열의 크기를 층수 자체의 효과로 비교해서는 안 됩니다.

{markdown_table(['층구간', 'SK 건수(비중)', '센트럴 건수(비중)', 'SK 중위(억)', '센트럴 중위(억)', '매칭 SK/센트럴 건수', '매칭 월 수', '매칭 평균 차이(억)'], floor_rows)}

주 분석에는 공통 표본 {len(common)}건 중 **{len(matched)}건(SKVIEW {primary['sk_n']}건·센트럴아이파크자이 {primary['ip_n']}건), {len(cells)}개 월×층구간, {primary['months']}개월**을 사용했습니다. 각 구간의 평균 차이를 `n_SK × n_센트럴 / (n_SK + n_센트럴)`로 가중하며, 이는 `가격 ~ 단지 + C(월×층구간)` 회귀의 단지 계수와 같습니다.

{conclusion}

구간별 평균 차이가 양수인 구간은 {positive_cells}개, 음수인 구간은 {negative_cells}개입니다. 다만 {sparse_cells}개 구간은 한쪽 거래가 1건뿐이고, 양쪽 각 3건 이상인 구간은 {well_sampled_cells}개입니다. 구간들이 같은 월의 시장 여건을 공유하므로 이 건수를 독립적인 반복 검증 횟수로 볼 수 없습니다. **{precision_note}**

## 5. 거래 선택과 모형에 따른 민감도

{sensitivity_note}

{markdown_table(['조건', 'SK/센트럴 건수', '월 수', '추정 차이', '95% 구간', '단위', '상태'], model_rows)}

한 달씩 제외한 주 분석의 추정 범위는 **{leave_text}**입니다. 특정 관측월 하나가 전체 결과에 미치는 영향을 점검한 값이며 신뢰구간은 아닙니다. 월별 각 3/5건 기준은 공통 층 범위로 제한한 뒤 월×층구간 매칭 전에 적용합니다. 단지별 양끝 1% 제외는 공통 표본의 가격 분위수를 사용한 민감도 확인이며 자동 오류 정제가 아닙니다.

{chart('model_sensitivity.png')}

## 6. 데이터에 대한 종합 평가

- **대표 가격대:** {sensitivity_note}
- **시기별 변화:** 분기별 표본의 중위가격은 거래가 집중된 가격대의 이동을 보여줍니다. 거래 주택과 층 구성이 달라 월 중위가격의 차이가 움직인 만큼 같은 조건 주택의 차이도 움직였다고 볼 수는 없습니다.
- **세부 구간의 정밀도:** 매칭 구간 {len(cells)}개 중 {sparse_cells}개는 한쪽 거래가 1건입니다. 전체 표본의 가격 차이를 특정 동·층에 그대로 적용하기에는 관측이 부족합니다.
- **거래 활동:** 관측 거래 수와 그 증감은 확인할 수 있습니다. 84㎡ 세대수, 매물 수, 매각 소요기간이 없어 회전율이나 매도 용이성을 계산할 수는 없습니다.

{area_note} 동·향·조망·수리상태·세부 평면·매도 사유는 분석에서 보정하지 못했습니다. 따라서 관측된 가격 차이의 원인을 입지나 브랜드로 분리하거나, 향후 가격 차이와 투자 수익을 예측하는 데에는 추가 자료가 필요합니다.

## 7. 산출 기준과 재현

공개 거래키는 계약일·지역코드·법정동·지번·단지·층·전용면적·금액으로 구성합니다. 동·호/계약 고유번호가 충분하지 않아 동일 조건의 서로 다른 실제 거래를 완전히 구분할 수는 없습니다. 원자료의 행 번호와 제공되는 동·등기일 정보는 보존합니다.

95% 구간은 월 안의 오차 의존을 허용한 군집 표준오차, 소표본 보정, 월 수−1 자유도의 t 분포로 계산했습니다. 월 군집이 6개 미만이면 구간을 생략합니다. 이 최소 조건도 충분한 정밀도를 보장하지 않으며 월 간 자기상관·거래 선택 편향·누락 변수는 구간에 반영되지 않습니다. HC3는 거래별 독립성을 더 강하게 가정한 보조 구간입니다. 구현은 [statsmodels 공식 문서](https://www.statsmodels.org/dev/generated/statsmodels.regression.linear_model.OLSResults.get_robustcov_results.html)를 따릅니다.

로그가격의 결과는 `100 × (exp(계수)−1)`로 환산한 조건부 기하평균 가격 차이(%)입니다. 개별 거래의 수익률이나 산술평균 가격 차이와는 다릅니다.

```bash
conda run -n py312 python analysis/compare_complexes_reassessment.py --as-of {metadata['as_of']} --start-date {metadata['start_date']} --lag-days {metadata['lag_days']} --source {metadata.get('source', 'db')}
```

[유효 거래](cleaned_transactions.csv), [가격 분포](price_distributions.csv), [분기별 관측](quarterly.csv), [층별 구성](floor_summary.csv), [월×층구간](matched_cells.csv), [월별 매칭 집계](matched_monthly.csv), [민감도 수치](model_comparison.csv), [한 달씩 제외한 결과](leave_one_month_out.csv), [설정·감사·원자료 해시](results.json)에서 수치를 확인할 수 있습니다. 실행 Python은 {metadata['python']}입니다.

## 부록. 월별 전 층 유효 거래

월별 표는 전체 유효 거래를 사용합니다. 희소 표시는 한쪽 3건 미만, 잠정 표시는 주 분석의 관측 완충기간 때문에 제외한 월입니다. 거래가 없는 달의 가격과 차이는 결측으로 남깁니다.

{markdown_table(['월', 'SK/센트럴 건수', 'SK 중위(억)', '센트럴 중위(억)', '중위 차이(억)', '주의'], month_rows)}
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
    charts = []
    if not result["common"].empty:
        fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
        rng = np.random.default_rng(0)
        for i, (name, color) in enumerate([(SK, "#2563eb"), (IP, "#c65b20")], 1):
            prices = result["common"].loc[result["common"]["aptNm"] == name, "price"]
            ax.scatter(i + rng.uniform(-.15, .15, len(prices)), prices, alpha=.35, s=18, color=color)
            ax.boxplot([prices], positions=[i], widths=.4, showfliers=False, manage_ticks=False,
                       boxprops={"color": color}, medianprops={"color": "black", "linewidth": 2})
        ax.set_xticks([1, 2], ["SKVIEW", "Central Ipark Xi"])
        ax.set(ylabel="Price (KRW 100m)", title="Common observed months and overlapping floor range")
        ax.grid(axis="y", alpha=.2)
        fig.savefig(chart_dir / "price_distribution.png", dpi=160)
        plt.close(fig)
        charts.append(("공통 표본의 가격 분포: 점은 거래, 상자는 중앙 50%", "price_distribution.png"))
    m = result["monthly"]
    dates = pd.to_datetime(m["month"])
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True, constrained_layout=True)
    for prefix, label, color in [("sk", "SKVIEW", "#2563eb"), ("ip", "Central Ipark Xi", "#c65b20")]:
        axes[0].plot(dates, m[prefix + "_median"], ".-", label=label, color=color)
        shift = pd.Timedelta(days=-5 if prefix == "sk" else 5)
        axes[2].bar(dates + shift, m[prefix + "_n"], width=9, color=color, label=label)
    axes[0].set(title="84㎡: monthly transaction prices and counts", ylabel="Median price (KRW 100m)")
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
    charts.append(("월별 가격·격차·거래 수(붉은 영역: 잠정월)", "monthly_observations.png"))
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
    _, db_clean, duplicates, audit = prepare_data(raw, start, as_of)
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
    result = analyze(clean, start, as_of, args.lag_days)
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
                        ("price_distributions", result["distributions"]), ("quarterly", result["quarters"]),
                        ("floor_summary", result["floors"]), ("matched_monthly", result["matched_months"]),
                        ("leave_one_month_out", pd.DataFrame(result["leave_one_month_out"]))]:
        frame.to_csv(output / f"{name}.csv", index=False, encoding="utf-8-sig")
    metadata = {"start_date": args.start_date, "as_of": args.as_of, "lag_days": args.lag_days, "source": args.source,
                "csv_dir": str(args.csv_dir.resolve()), "csv_audit": csv_audit,
                "primary_cutoff": str(result["cutoff"].date()), "database": str(args.db_path.resolve()),
                "database_sha256": hashlib.sha256(args.db_path.read_bytes()).hexdigest(),
                "query_rows_sha256": hashlib.sha256(raw.to_json(orient="split", force_ascii=False).encode()).hexdigest(),
                "python": sys.version.split()[0], "executable": sys.executable,
                "versions": {name: importlib.metadata.version(name) for name in ["pandas", "numpy", "statsmodels"]}}
    document = {"metadata": metadata, "audit": audit, "primary": result["primary"], "models": result["models"],
                "leave_one_month_out": result["leave_one_month_out"]}
    (output / "results.json").write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    charts = [] if args.no_charts else make_charts(result, output)
    (output / "report.md").write_text(render_report(clean, audit, result, metadata, charts), encoding="utf-8")
    print(f"보고서: {output / 'report.md'}")
    print(f"정리 후 거래: SK {int((clean.aptNm == SK).sum())}건 / 센트럴 {int((clean.aptNm == IP).sum())}건")
    print(json.dumps(result["primary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
