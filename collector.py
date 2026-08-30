import os
import sys
import math
import yaml
import requests
import xmltodict
import pandas as pd
from urllib.parse import unquote
from datetime import datetime, timezone, timedelta
from db_manager import RealEstateDB

# .env 파일이 존재하는 경우 환경변수로 자동 로드 (로컬 테스트용)
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())
except ImportError:
    pass

# 한국 표준시 (KST = UTC+9)
KST = timezone(timedelta(hours=9))

# 국토교통부 아파트매매 실거래 자료 API URL
API_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"


def load_config(config_path="setting.yml"):
    """setting.yml 설정 파일을 로드합니다."""
    if not os.path.exists(config_path):
        print(f"[경고] 설정 파일({config_path})이 없습니다. 기본 설정을 사용합니다.")
        return {
            "collection": {
                "start_year_month": "202601",
                "recent_months_buffer": 2,
                "regions": [
                    {"code": "41115", "name": "수원시 팔달구"},
                    {"code": "41117", "name": "수원시 영통구"},
                ],
                "area_filter": {
                    "enabled": True,
                    "types": [{"name": "84타입", "min": 84.0, "max": 85.0}],
                },
                "build_year_filter": {"enabled": True, "within_years": 10},
                "target_complexes": [],
            },
            "storage": {"db_path": "data/transactions.db", "table_name": "transactions"},
        }
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_kst_now():
    """한국 시간(KST) 기준 현재 datetime 반환"""
    return datetime.now(KST)


def generate_year_month_list(start_ym: str, end_ym: str) -> list:
    """
    시작 년월(YYYYMM)부터 종료 년월(YYYYMM)까지의 년월 문자열 리스트를 생성합니다.
    예: ('202601', '202603') -> ['202601', '202602', '202603']
    """
    start_dt = datetime.strptime(start_ym, "%Y%m")
    end_dt = datetime.strptime(end_ym, "%Y%m")

    if start_dt > end_dt:
        return [end_ym]

    ym_list = []
    curr = start_dt
    while curr <= end_dt:
        ym_list.append(curr.strftime("%Y%m"))
        # 다음 달 계산
        year = curr.year + (curr.month // 12)
        month = (curr.month % 12) + 1
        curr = datetime(year, month, 1)

    return ym_list


def get_rolling_year_month_list(buffer_months: int = 2) -> list:
    """
    한국 시간 기준 당월(0) 및 직전 N개 월의 년월 리스트를 반환합니다.
    (예: 8월 기준 buffer_months=2 -> ['202606', '202607', '202608'])
    """
    kst_now = get_kst_now()
    ym_set = set()

    for i in range(buffer_months + 1):
        # i개월 전 날짜 계산
        year = kst_now.year
        month = kst_now.month - i
        while month <= 0:
            month += 12
            year -= 1
        ym_set.add(f"{year}{month:02d}")

    return sorted(list(ym_set))


def fetch_page(api_key: str, lawd_cd: str, deal_ymd: str, page_no: int = 1, num_of_rows: int = 1000):
    """
    단일 페이지의 공공데이터포털 실거래가 API를 호출합니다.
    (반환: items_list, total_count, result_code, result_msg)
    """
    clean_service_key = unquote(api_key.strip())

    params = {
        "serviceKey": clean_service_key,
        "LAWD_CD": str(lawd_cd),
        "DEAL_YMD": str(deal_ymd),
        "pageNo": str(page_no),
        "numOfRows": str(num_of_rows),
    }

    try:
        res = requests.get(API_URL, params=params, timeout=15)
        # 만약 401/403 등 게이트웨이 키 인코딩 이슈 발생 시 raw url 로 2차 시도
        if res.status_code in [401, 403] or "SERVICE_KEY_IS_NULL" in res.text:
            raw_url = f"{API_URL}?serviceKey={api_key.strip()}&LAWD_CD={lawd_cd}&DEAL_YMD={deal_ymd}&pageNo={page_no}&numOfRows={num_of_rows}"
            res = requests.get(raw_url, timeout=15)

        data = xmltodict.parse(res.text)
    except Exception as e:
        print(f"  [API 호출 에러] {deal_ymd} / {lawd_cd} (Page {page_no}): {e}")
        return [], 0, "ERROR", str(e)

    # 게이트웨이 에러 체크
    if "OpenAPI_ServiceResponse" in data:
        header = data["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        err_msg = header.get("errMsg", "")
        return_msg = header.get("returnAuthMsg", "")
        print(f"  [공공데이터포털 게이트웨이 에러] {err_msg} ({return_msg})")
        print("  💡 공공데이터포털 신규 API 키는 서버 동기화에 최대 1시간이 소요될 수 있습니다.")
        return [], 0, "AUTH_ERROR", f"{err_msg}: {return_msg}"

    response = data.get("response", {})
    header = response.get("header", {})
    result_code = header.get("resultCode", "")
    result_msg = header.get("resultMsg", "")

    # 정상 응답 코드 (00 또는 000)
    if result_code not in ["00", "000", "INFO-000"]:
        print(f"  [API 응답 코드 오류] Code: {result_code}, Msg: {result_msg}")
        return [], 0, result_code, result_msg

    body = response.get("body", {})
    total_count = int(body.get("totalCount", 0))

    items = body.get("items", {})
    if not items or "item" not in items:
        return [], total_count, result_code, result_msg

    raw_items = items["item"]
    items_list = [raw_items] if isinstance(raw_items, dict) else raw_items
    return items_list, total_count, result_code, result_msg


def fetch_all_pages_for_month(api_key: str, lawd_cd: str, region_name: str, deal_ymd: str) -> list:
    """
    특정 년월(DEAL_YMD) 및 지역(LAWD_CD)에 대해 전체 페이지를 순회하며 데이터를 수집합니다.
    """
    num_of_rows = 1000
    all_items = []

    # 1페이지 호출
    items, total_count, code, msg = fetch_page(api_key, lawd_cd, deal_ymd, page_no=1, num_of_rows=num_of_rows)
    if code not in ["00", "000", "INFO-000"]:
        return []

    all_items.extend(items)

    if total_count == 0:
        return []

    total_pages = math.ceil(total_count / num_of_rows)
    print(f"  > [{region_name} ({lawd_cd})] {deal_ymd}: 총 {total_count}건 (전체 {total_pages}페이지 중 1페이지 완료)")

    # 2페이지 이상일 경우 순차 호출
    for page in range(2, total_pages + 1):
        p_items, _, p_code, _ = fetch_page(api_key, lawd_cd, deal_ymd, page_no=page, num_of_rows=num_of_rows)
        if p_code in ["00", "000", "INFO-000"] and p_items:
            all_items.extend(p_items)
            print(f"    - {page}/{total_pages} 페이지 수집 완료 ({len(p_items)}건)")

    return all_items


def clean_and_filter(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    수집된 원본 데이터프레임을 전처리하고 설정(면적, 준공연도, 단지)에 맞게 필터링합니다.
    """
    if df.empty:
        return df

    # 1. 텍스트 및 기본 컬럼 전처리
    str_cols = ["aptNm", "umdNm", "jibun", "dealYear", "dealMonth", "dealDay", "sggCd", "buildYear", "dealType"]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # 2. 거래금액(dealAmount) 숫자 변환 (콤마 제거)
    if "dealAmount" in df.columns:
        df["dealAmount"] = (
            df["dealAmount"]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.strip()
            .astype(float)
            .astype(int)
        )

    # 3. 전용면적(excluUseAr) 숫자(float) 변환
    if "excluUseAr" in df.columns:
        df["excluUseAr"] = pd.to_numeric(df["excluUseAr"], errors="coerce")

    # 4. 층수(floor) 숫자 변환
    if "floor" in df.columns:
        df["floor"] = pd.to_numeric(df["floor"], errors="coerce")

    # 5. 거래일자(dealDate) 생성 (YYYY-MM-DD 형식)
    if all(c in df.columns for c in ["dealYear", "dealMonth", "dealDay"]):
        df["dealDate"] = pd.to_datetime(
            df["dealYear"].astype(str)
            + "-"
            + df["dealMonth"].astype(str).str.zfill(2)
            + "-"
            + df["dealDay"].astype(str).str.zfill(2),
            errors="coerce",
        )

    # 6. 전용면적 필터 적용 (setting.yml)
    area_filter = config.get("collection", {}).get("area_filter", {})
    if area_filter.get("enabled", False):
        types = area_filter.get("types", [])
        if types and "excluUseAr" in df.columns:
            condition = pd.Series(False, index=df.index)
            for t in types:
                min_ar = float(t.get("min", 0))
                max_ar = float(t.get("max", 999999))
                type_cond = (df["excluUseAr"] >= min_ar) & (df["excluUseAr"] < max_ar)
                condition = condition | type_cond
                # 타입명 컬럼 추가
                df.loc[type_cond, "areaType"] = t.get("name", f"{min_ar}~{max_ar}㎡")
            df = df[condition]

    # 7. 준공연도(건축년도) 필터 적용 (setting.yml)
    build_filter = config.get("collection", {}).get("build_year_filter", {})
    if build_filter.get("enabled", False) and "buildYear" in df.columns:
        within_years = int(build_filter.get("within_years", 10))
        current_year = get_kst_now().year
        min_build_year = current_year - within_years

        # 숫자로 변환 후 필터링 (결측치 제외)
        build_numeric = pd.to_numeric(df["buildYear"], errors="coerce")
        df = df[build_numeric >= min_build_year]

    # 8. 관심 단지 필터 적용 (target_complexes)
    target_complexes = config.get("collection", {}).get("target_complexes", [])
    if target_complexes and "aptNm" in df.columns:
        df = df[df["aptNm"].isin(target_complexes)]

    return df


def run():
    print("=" * 60)
    print("🚀 [스마트 실거래가 수집 파이프라인 시작 (30일 신고유예 롤링 갱신)]")
    print("=" * 60)

    # 1. API 키 확인
    api_key = os.environ.get("DATA_GO_KR_API_KEY")
    if not api_key:
        print("[오류] 환경변수 DATA_GO_KR_API_KEY 가 설정되지 않았습니다.")
        print(".env 파일 또는 GitHub Repository Secrets 에 키를 등록해주세요.")
        sys.exit(1)

    # 2. 설정 로드
    config = load_config("setting.yml")
    collection_cfg = config.get("collection", {})
    storage_cfg = config.get("storage", {})

    start_ym = collection_cfg.get("start_year_month", "202601")
    buffer_months = int(collection_cfg.get("recent_months_buffer", 2))
    regions = collection_cfg.get("regions", [{"code": "41115", "name": "수원시 팔달구"}])
    db_path = storage_cfg.get("db_path", "data/transactions.db")

    # 한국 시간 기준 오늘
    kst_today = get_kst_now()
    end_ym = kst_today.strftime("%Y%m")

    # DB 인스턴스 초기화
    db = RealEstateDB(db_path=db_path)
    before_total_cnt = db.get_count()

    print(f"📅 실행 기준 시점 (KST): {kst_today.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"💾 대상 데이터베이스: {db_path} (현재 적재 건수: {before_total_cnt}건)")
    print(f"⏱️ 30일 신고 지연 대응 롤링 버퍼: 최근 {buffer_months + 1}개월")
    print("-" * 60)

    # 3. 지역별 맞춤 기간 수집 (DB 존재 여부 기반 하이브리드 수집)
    raw_collected_items = []
    
    for r in regions:
        code = str(r.get("code"))
        name = r.get("name", code)
        has_data = db.has_region_data(code)

        if has_data:
            # 기존 수집 지역: 당월 + 직전 buffer_months 개월 (30일 의무 신고 지연분 및 수정건 갱신)
            target_ym_list = get_rolling_year_month_list(buffer_months)
            mode_desc = f"🔄 롤링 갱신 ({len(target_ym_list)}개 월: {target_ym_list})"
        else:
            # 신규 지역 / 최초 수집: start_year_month 부터 전체 수집
            target_ym_list = generate_year_month_list(start_ym, end_ym)
            mode_desc = f"⚡ 초기 전체 수집 ({len(target_ym_list)}개 월: {start_ym}~{end_ym})"

        print(f"📍 [{name} ({code})] 모드: {mode_desc}")

        for ym in target_ym_list:
            items = fetch_all_pages_for_month(api_key, code, name, ym)
            if items:
                for item in items:
                    item["regionName"] = name
                raw_collected_items.extend(items)

    print("-" * 60)
    print(f"📦 API 원본 수집 데이터: 총 {len(raw_collected_items)}건")

    if not raw_collected_items:
        print("ℹ️ 신규 수집된 원본 데이터가 없습니다.")
        new_df = pd.DataFrame()
    else:
        raw_df = pd.DataFrame(raw_collected_items)
        new_df = clean_and_filter(raw_df, config)
        print(f"✨ 필터링 및 전처리 완료 데이터: {len(new_df)}건")

    # 4. RealEstateDB 클래스를 통한 SQLite UPSERT 적재
    if not new_df.empty:
        total_cnt, inserted_cnt = db.upsert_transactions(new_df)
        print(f"💾 SQLite 적재 완료: 기존 {before_total_cnt}건 -> 최종 {total_cnt}건 (신규/갱신: {inserted_cnt}건)")
    else:
        print(f"ℹ️ 적재할 신규 데이터가 없습니다. (현재 DB 총 건수: {before_total_cnt}건)")

    print(f"🎉 파이프라인 정상 종료: {db_path}")
    print("=" * 60)


if __name__ == "__main__":
    run()
