#!/usr/bin/env python3
"""
[과거 실거래가 대량 수집 독립 스크립트]
- 기존 수집기(collector.py) 및 설정(setting.yml)을 전혀 변경하지 않고 독립적으로 실행됩니다.
- 2020년 1월부터 현재까지 팔달구(41115) 및 영통구(41117)의 모든 실거래가 데이터를 수집하여 DB에 적재합니다.
- 기본값으로 면적/연식 필터를 해제하여 '모든 거래'를 수집합니다.
- 복합 UNIQUE 인덱스(UPSERT)를 통해 기존 DB(data/transactions.db)에 안전하게 병합됩니다.

사용법:
    python scripts/collect_historical.py
    python scripts/collect_historical.py --start-ym 202001 --end-ym 202609
    python scripts/collect_historical.py --db-path data/transactions_all.db (별도 DB 저장 시)
    python scripts/collect_historical.py --help
"""

import os
import sys
import time
import math
import argparse
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote
import pandas as pd
import requests
import xmltodict
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 프로젝트 루트를 sys.path에 추가하여 기존 모듈(RealEstateDB) 재사용
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())
except ImportError:
    pass

from src.db.db_manager import RealEstateDB

KST = timezone(timedelta(hours=9))
API_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"

TARGET_REGIONS = [
    {"code": "41115", "name": "수원시 팔달구"},
    {"code": "41117", "name": "수원시 영통구"},
]


def get_kst_now():
    return datetime.now(KST)


def get_retry_session():
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def generate_year_month_list(start_ym: str, end_ym: str) -> list:
    """start_ym부터 end_ym까지의 년월 리스트 생성 (예: '202001' ~ '202609')"""
    start_dt = datetime.strptime(start_ym, "%Y%m")
    end_dt = datetime.strptime(end_ym, "%Y%m")

    if start_dt > end_dt:
        return [end_ym]

    ym_list = []
    curr = start_dt
    while curr <= end_dt:
        ym_list.append(curr.strftime("%Y%m"))
        year = curr.year + (curr.month // 12)
        month = (curr.month % 12) + 1
        curr = datetime(year, month, 1)

    return ym_list


def fetch_page(api_key: str, lawd_cd: str, deal_ymd: str, page_no: int = 1, num_of_rows: int = 200, timeout: int = 50):
    clean_service_key = unquote(api_key.strip())
    params = {
        "serviceKey": clean_service_key,
        "LAWD_CD": str(lawd_cd),
        "DEAL_YMD": str(deal_ymd),
        "pageNo": str(page_no),
        "numOfRows": str(num_of_rows),
    }

    session = get_retry_session()
    try:
        res = session.get(API_URL, params=params, timeout=timeout)
        if res.status_code in [401, 403] or "SERVICE_KEY_IS_NULL" in res.text:
            raw_url = f"{API_URL}?serviceKey={api_key.strip()}&LAWD_CD={lawd_cd}&DEAL_YMD={deal_ymd}&pageNo={page_no}&numOfRows={num_of_rows}"
            res = session.get(raw_url, timeout=timeout)

        data = xmltodict.parse(res.text)
    except Exception as e:
        return [], 0, "ERROR", str(e)

    if "OpenAPI_ServiceResponse" in data:
        header = data["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        err_msg = header.get("errMsg", "")
        return_msg = header.get("returnAuthMsg", "")
        return [], 0, "AUTH_ERROR", f"{err_msg}: {return_msg}"

    response = data.get("response", {})
    header = response.get("header", {})
    result_code = header.get("resultCode", "")
    result_msg = header.get("resultMsg", "")

    if result_code not in ["00", "000", "INFO-000"]:
        return [], 0, result_code, result_msg

    body = response.get("body", {})
    total_count = int(body.get("totalCount", 0))

    items = body.get("items", {})
    if not items or "item" not in items:
        return [], total_count, result_code, result_msg

    raw_items = items["item"]
    items_list = [raw_items] if isinstance(raw_items, dict) else raw_items
    return items_list, total_count, result_code, result_msg


def fetch_month_data(api_key: str, lawd_cd: str, region_name: str, deal_ymd: str, delay: float = 0.2, max_retries: int = 3, num_of_rows: int = 200, timeout: int = 50) -> list:
    all_items = []
    items = []
    total_count = 0
    code = ""
    msg = ""

    for attempt in range(1, max_retries + 1):
        items, total_count, code, msg = fetch_page(api_key, lawd_cd, deal_ymd, page_no=1, num_of_rows=num_of_rows, timeout=timeout)
        if code in ["00", "000", "INFO-000"]:
            break
        if attempt < max_retries:
            print(f"    ⚠️ [{region_name}] {deal_ymd} ({code}: {msg}) - 재시도 {attempt}/{max_retries}...")
            time.sleep(1.5 * attempt)

    if code not in ["00", "000", "INFO-000"]:
        print(f"    ❌ [{region_name}] {deal_ymd} API 오류: ({code}) {msg}")
        return []

    all_items.extend(items)
    if total_count == 0:
        return []

    total_pages = math.ceil(total_count / num_of_rows)
    if total_pages > 1:
        print(f"    📄 [{region_name}] {deal_ymd}: 총 {total_count}건 ({num_of_rows}건씩 총 {total_pages}페이지 분할 수집)")
        for page in range(2, total_pages + 1):
            time.sleep(delay)
            p_items = []
            for attempt in range(1, max_retries + 1):
                p_items, _, p_code, p_msg = fetch_page(api_key, lawd_cd, deal_ymd, page_no=page, num_of_rows=num_of_rows, timeout=timeout)
                if p_code in ["00", "000", "INFO-000"] and p_items:
                    all_items.extend(p_items)
                    break
                if attempt < max_retries:
                    time.sleep(1.5 * attempt)

    return all_items


def process_items_to_df(items: list, region_name: str, only_84: bool = False, within_years: int = None) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()

    df = pd.DataFrame(items)

    # 1. 텍스트 컬럼 공백 제거
    str_cols = [
        "aptNm", "umdNm", "jibun", "dealYear", "dealMonth", "dealDay",
        "sggCd", "buildYear", "dealType", "cdealType", "cdealDay"
    ]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    if "dealMonth" in df.columns:
        df["dealMonth"] = df["dealMonth"].str.zfill(2)
    if "dealDay" in df.columns:
        df["dealDay"] = df["dealDay"].str.zfill(2)

    # 거래유형 기본값: None/빈값/- 인 경우 '중개거래'
    if "dealType" in df.columns:
        df["dealType"] = df["dealType"].astype(str).str.strip()
        invalid_type = df["dealType"].isna() | df["dealType"].isin(["None", "nan", "", "-", "NoneType"])
        df["dealType"] = df["dealType"].mask(invalid_type, "중개거래")
    else:
        df["dealType"] = "중개거래"

    # 2. 거래금액(dealAmount) 정수 변환
    if "dealAmount" in df.columns:
        df["dealAmount"] = (
            df["dealAmount"]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.strip()
            .astype(float)
            .astype(int)
        )

    if "excluUseAr" in df.columns:
        df["excluUseAr"] = pd.to_numeric(df["excluUseAr"], errors="coerce")

    if "floor" in df.columns:
        df["floor"] = pd.to_numeric(df["floor"], errors="coerce").fillna(0).astype(int)

    # 계약일자 (dealDate) 생성 YYYY-MM-DD
    if all(k in df.columns for k in ["dealYear", "dealMonth", "dealDay"]):
        df["dealDate"] = (
            df["dealYear"].astype(str).str.zfill(4)
            + "-"
            + df["dealMonth"].astype(str).str.zfill(2)
            + "-"
            + df["dealDay"].astype(str).str.zfill(2)
        )

    # 지역명 부여
    df["regionName"] = region_name

    # 전용면적 타입 라벨링 (84타입, 59타입 등)
    def assign_area_type(area):
        if pd.isna(area):
            return "기타"
        if 59.0 <= area < 60.0:
            return "59타입"
        elif 84.0 <= area < 85.0:
            return "84타입"
        elif 74.0 <= area < 75.0:
            return "74타입"
        elif 101.0 <= area < 103.0:
            return "101타입"
        else:
            return f"{round(area)}㎡"

    df["areaType"] = df["excluUseAr"].apply(assign_area_type)

    if "cdealType" not in df.columns:
        df["cdealType"] = None
    if "cdealDay" not in df.columns:
        df["cdealDay"] = None

    # 1) 84타입 전용 필터링
    if only_84:
        df = df[(df["excluUseAr"] >= 84.0) & (df["excluUseAr"] < 85.0)]

    # 2) 준공연도 필터링 (within_years 지정 시)
    if within_years and "buildYear" in df.columns:
        current_year = get_kst_now().year
        valid_by = pd.to_numeric(df["buildYear"], errors="coerce")
        df = df[valid_by >= (current_year - within_years)]

    return df


def main():
    parser = argparse.ArgumentParser(description="수원시 팔달구·영통구 2020년 이후 실거래가 수집기")
    parser.add_argument("--start-ym", default="202001", help="수집 시작 년월 (YYYYMM, 기본: 202001)")
    parser.add_argument("--end-ym", default=None, help="수집 종료 년월 (YYYYMM, 기본: 현재 년월)")
    parser.add_argument("--db-path", default="data/transactions.db", help="저장할 SQLite DB 경로 (기본: data/transactions.db)")
    parser.add_argument("--delay", type=float, default=0.2, help="API 호출 간 대기시간(초, 기본 0.2초)")
    parser.add_argument("--timeout", type=int, default=50, help="API 응답 타임아웃(초, 기본 50초)")
    parser.add_argument("--num-of-rows", type=int, default=200, help="1회 API 호출 시 요청할 행 수 (기본 200건, 서버 부담 완화)")
    parser.add_argument("--only-84", action="store_true", help="⭐ 84타입(전용 84.0㎡ ~ 85.0㎡ 미만)만 선별 수집")
    parser.add_argument("--within-years", type=int, default=None, help="최근 N년 이내 준공 아파트만 수집 (예: 10)")
    parser.add_argument("--apply-filters", action="store_true", help="기존 setting.yml 기준(84타입 & 10년 이내 신축) 동시 적용")

    args = parser.parse_args()

    api_key = os.getenv("DATA_GO_KR_API_KEY")
    if not api_key:
        print("❌ [오류] 환경변수 DATA_GO_KR_API_KEY 가 설정되어 있지 않습니다.")
        print("   .env 파일에 DATA_GO_KR_API_KEY=발급키 를 작성해 주세요.")
        sys.exit(1)

    now = get_kst_now()
    start_ym = args.start_ym
    end_ym = args.end_ym or now.strftime("%Y%m")

    ym_list = generate_year_month_list(start_ym, end_ym)
    total_months = len(ym_list)
    total_tasks = total_months * len(TARGET_REGIONS)

    only_84 = args.only_84 or args.apply_filters
    within_years = args.within_years or (10 if args.apply_filters else None)

    filter_desc = []
    if only_84:
        filter_desc.append("84타입(84~85㎡)")
    if within_years:
        filter_desc.append(f"{within_years}년 이내 신축")
    mode_text = " + ".join(filter_desc) if filter_desc else "모든 평형 & 모든 연식 전체 수집 (필터 없음)"

    print("=" * 70)
    print("🚀 [수원시 팔달구 & 영통구 실거래가 수집기]")
    print(f"  - 수집 기간: {start_ym} ~ {end_ym} (총 {total_months}개월)")
    print(f"  - 대상 지역: {', '.join([r['name'] for r in TARGET_REGIONS])}")
    print(f"  - 필터 모드: {mode_text}")
    print(f"  - 요청 설정: 1회당 {args.num_of_rows}건씩 요청 / 타임아웃 {args.timeout}초")
    print(f"  - 저장 DB: {args.db_path}")
    print("=" * 70)

    db = RealEstateDB(db_path=args.db_path)

    start_time = time.time()
    task_count = 0
    total_collected_rows = 0

    for region in TARGET_REGIONS:
        region_code = region["code"]
        region_name = region["name"]
        print(f"\n📍 [{region_name} ({region_code})] 수집 시작...")

        for ym in ym_list:
            task_count += 1
            progress_pct = (task_count / total_tasks) * 100
            
            raw_items = fetch_month_data(
                api_key, region_code, region_name, ym,
                delay=args.delay,
                num_of_rows=args.num_of_rows,
                timeout=args.timeout
            )
            if raw_items:
                df = process_items_to_df(raw_items, region_name, only_84=only_84, within_years=within_years)
                if not df.empty:
                    saved_count, added_cnt = db.upsert_transactions(df)
                    total_collected_rows += len(df)
                    print(f"  [{task_count:3d}/{total_tasks:3d}] ({progress_pct:5.1f}%) {ym}: 수집 {len(df):3d}건 -> DB 적재 완료 (누적 {saved_count:,}건)")
                else:
                    print(f"  [{task_count:3d}/{total_tasks:3d}] ({progress_pct:5.1f}%) {ym}: 조건 만족 데이터 없음 (원자료 {len(raw_items)}건)")
            else:
                print(f"  [{task_count:3d}/{total_tasks:3d}] ({progress_pct:5.1f}%) {ym}: 거래 데이터 없음")

            time.sleep(args.delay)

    elapsed = time.time() - start_time
    print("\n" + "=" * 70)
    print("🎉 [수집 완료]")
    print(f"  - 소요 시간: {elapsed / 60:.1f}분")
    print(f"  - 수집 데이터: 총 {total_collected_rows:,}건 처리 완료")
    print(f"  - DB 최종 저장 건수: {db.get_count():,}건")
    print("=" * 70)


if __name__ == "__main__":
    main()
