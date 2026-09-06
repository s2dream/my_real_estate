#!/usr/bin/env python3
"""
[국토교통부 실거래가 공개시스템 CSV 파일 일괄 DB 적재기]
- 국토교통부 실거래가 공개시스템(https://rt.molit.go.kr)에서 다운로드받은 CSV 파일을 1초 만에 DB에 적재합니다.
- 복수 파일 일괄 처리 지원 (폴더 지정 시 폴더 내 모든 CSV 자동 감지)
- 인코딩(CP949 / EUC-KR / UTF-8) 및 상단 메타데이터 헤더 자동 인식
- 기존 DB(data/transactions.db)에 중복 없이 안전하게 멱등 적재(UPSERT)

사용법:
    # 1) data/csv 폴더 내 모든 CSV 파일 적재 (기본)
    python scripts/import_csv.py

    # 2) 84타입만 선별 적재
    python scripts/import_csv.py --only-84

    # 3) 특정 CSV 파일 직접 지정
    python scripts/import_csv.py --file data/csv/아파트(매매)_실거래가_2024.csv

    # 4) 특정 폴더 지정
    python scripts/import_csv.py --dir ~/Downloads
"""

import os
import sys
import glob
import argparse
import pandas as pd
from datetime import datetime, timezone, timedelta

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.db.db_manager import RealEstateDB

KST = timezone(timedelta(hours=9))

# 수원시 구별 시군구 코드 매핑
REGION_CODE_MAP = {
    "팔달구": ("41115", "수원시 팔달구"),
    "영통구": ("41117", "수원시 영통구"),
    "장안구": ("41111", "수원시 장안구"),
    "권선구": ("41113", "수원시 권선구"),
}


def read_molit_csv(file_path: str) -> pd.DataFrame:
    """
    국토부 CSV 파일의 인코딩과 상단 안내문(헤더 오프셋)을 자동 감지하여 DataFrame으로 읽어옵니다.
    """
    encodings = ["cp949", "euc-kr", "utf-8-sig", "utf-8"]
    selected_encoding = None
    header_line = None

    # 1. 인코딩 및 '단지명' 헤더 라인 번호 자동 감지
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                for idx, line in enumerate(f):
                    if "단지명" in line and ("전용면적" in line or "거래금액" in line):
                        header_line = idx
                        selected_encoding = enc
                        break
            if selected_encoding:
                break
        except (UnicodeDecodeError, Exception):
            continue

    if header_line is None or selected_encoding is None:
        raise ValueError(f"'{file_path}' 파일에서 국토부 실거래가 헤더('단지명')를 찾을 수 없습니다.")

    # 2. DataFrame 로드
    df = pd.read_csv(file_path, skiprows=header_line, encoding=selected_encoding, dtype=str)
    return df


def parse_and_transform(df: pd.DataFrame, only_84: bool = False, target_regions: list = None) -> pd.DataFrame:
    """
    국토부 CSV 형식 컬럼을 우리 시스템의 DB 스키마 컬럼으로 변환합니다.
    """
    if df.empty:
        return pd.DataFrame()

    # 컬럼명 공백 제거
    df.columns = [c.strip() for c in df.columns]

    # 컬럼 매핑 확인 (컬럼명 유연 대응)
    col_map = {}
    for col in df.columns:
        if "단지명" in col:
            col_map["aptNm"] = col
        elif "시군구" in col:
            col_map["sigungu"] = col
        elif "번지" in col:
            col_map["jibun"] = col
        elif "전용면적" in col:
            col_map["excluUseAr"] = col
        elif "계약년월" in col:
            col_map["dealYM"] = col
        elif "계약일" in col:
            col_map["dealDay"] = col
        elif "거래금액" in col:
            col_map["dealAmount"] = col
        elif "층" in col and "건축" not in col:
            col_map["floor"] = col
        elif "건축년도" in col:
            col_map["buildYear"] = col
        elif "해제사유" in col or "해제여부" in col:
            col_map["cdealDay"] = col
        elif "거래유형" in col:
            col_map["dealType"] = col

    result = pd.DataFrame()

    # 1. 단지명
    result["aptNm"] = df[col_map["aptNm"]].astype(str).str.strip() if "aptNm" in col_map else ""

    # 2. 시군구 분석 (sggCd, regionName, umdNm)
    if "sigungu" in col_map:
        sgg_series = df[col_map["sigungu"]].astype(str).str.strip()
        
        def extract_sgg_info(s):
            # 예: "경기도 수원팔달구 화서동" 또는 "경기도 수원시 영통구 이의동"
            parts = s.split()
            umd = parts[-1] if len(parts) >= 2 else ""
            
            sgg_code = "41115"
            reg_name = "수원시 팔달구"
            for k, (code, name) in REGION_CODE_MAP.items():
                if k in s:
                    sgg_code = code
                    reg_name = name
                    break
            return pd.Series([sgg_code, reg_name, umd])

        sgg_info = sgg_series.apply(extract_sgg_info)
        result["sggCd"] = sgg_info[0]
        result["regionName"] = sgg_info[1]
        result["umdNm"] = sgg_info[2]
    else:
        result["sggCd"] = "41115"
        result["regionName"] = "수원시 팔달구"
        result["umdNm"] = ""

    # 3. 지번
    result["jibun"] = df[col_map["jibun"]].astype(str).str.strip() if "jibun" in col_map else ""

    # 4. 계약일자 처리
    if "dealYM" in col_map and "dealDay" in col_map:
        deal_ym = df[col_map["dealYM"]].astype(str).str.replace(r"\D", "", regex=True).str.strip()
        deal_d = df[col_map["dealDay"]].astype(str).str.replace(r"\D", "", regex=True).str.strip().str.zfill(2)
        
        result["dealYear"] = deal_ym.str[:4]
        result["dealMonth"] = deal_ym.str[4:6]
        result["dealDay"] = deal_d
        result["dealDate"] = result["dealYear"] + "-" + result["dealMonth"] + "-" + result["dealDay"]
    else:
        return pd.DataFrame()

    # 5. 거래금액
    if "dealAmount" in col_map:
        result["dealAmount"] = (
            df[col_map["dealAmount"]]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.strip()
            .astype(float)
            .astype(int)
        )

    # 6. 전용면적 & 타입
    if "excluUseAr" in col_map:
        result["excluUseAr"] = pd.to_numeric(df[col_map["excluUseAr"]], errors="coerce")
    else:
        result["excluUseAr"] = 0.0

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

    result["areaType"] = result["excluUseAr"].apply(assign_area_type)

    # 7. 층수
    if "floor" in col_map:
        result["floor"] = pd.to_numeric(df[col_map["floor"]], errors="coerce").fillna(0).astype(int)
    else:
        result["floor"] = 0

    # 8. 건축년도
    result["buildYear"] = df[col_map["buildYear"]].astype(str).str.strip() if "buildYear" in col_map else ""

    # 9. 거래유형
    if "dealType" in col_map:
        dtype_series = df[col_map["dealType"]].astype(str).str.strip()
        invalid_type = dtype_series.isna() | dtype_series.isin(["None", "nan", "", "-", "NoneType"])
        result["dealType"] = dtype_series.mask(invalid_type, "중개거래")
    else:
        result["dealType"] = "중개거래"

    # 10. 해제/취소건 처리
    if "cdealDay" in col_map:
        cday = df[col_map["cdealDay"]].astype(str).str.strip()
        is_canceled = (cday != "-") & (cday != "") & (cday.str.lower() != "nan")
        result["cdealDay"] = cday.where(is_canceled, None)
        result["cdealType"] = is_canceled.map({True: "O", False: None})
    else:
        result["cdealDay"] = None
        result["cdealType"] = None

    # 지역 필터링 (지정 시에만 적용)
    if target_regions:
        result = result[result["sggCd"].isin(target_regions)]

    # 84타입 필터링 옵션
    if only_84:
        result = result[(result["excluUseAr"] >= 84.0) & (result["excluUseAr"] < 85.0)]

    return result


def main():
    parser = argparse.ArgumentParser(description="국토교통부 실거래가 공개시스템 CSV 파일 일괄 DB 적재기")
    parser.add_argument("--file", default=None, help="적재할 특정 CSV 파일 경로")
    parser.add_argument("--dir", default="data/csv", help="CSV 파일들이 위치한 디렉토리 (기본: data/csv)")
    parser.add_argument("--db-path", default="data/transactions.db", help="저장할 SQLite DB 경로 (기본: data/transactions.db)")
    parser.add_argument("--only-84", action="store_true", help="⭐ 84타입(전용 84.0㎡ ~ 85.0㎡ 미만)만 선별 적재")

    args = parser.parse_args()

    # 대상 파일 수집
    csv_files = []
    if args.file:
        if os.path.exists(args.file):
            csv_files.append(args.file)
        else:
            print(f"❌ [오류] 지정한 파일이 존재하지 않습니다: {args.file}")
            sys.exit(1)
    else:
        search_dir = os.path.join(PROJECT_ROOT, args.dir) if not os.path.isabs(args.dir) else args.dir
        if os.path.exists(search_dir):
            csv_files = sorted(glob.glob(os.path.join(search_dir, "*.csv")) + glob.glob(os.path.join(search_dir, "*.CSV")))

    print("=" * 70)
    print("🚀 [국토교통부 실거래가 CSV 일괄 DB 적재기]")
    print(f"  - 검색 폴더: {args.dir}")
    print(f"  - 대상 파일: 총 {len(csv_files)}개 발견")
    print(f"  - 필터 모드: {'84타입(84~85㎡)만 선별 적재' if args.only_84 else '전체 평형 적재'}")
    print(f"  - 대상 DB: {args.db_path}")
    print("=" * 70)

    if not csv_files:
        print(f"\nℹ️ 적재할 CSV 파일이 없습니다.")
        print(f"👉 국토교통부 실거래가 공개시스템(https://rt.molit.go.kr)에서 다운로드받은 CSV 파일을")
        print(f"   '{args.dir}' 폴더에 넣은 후 다시 실행해 주세요!")
        print(f"   예: mkdir -p {args.dir} && mv ~/Downloads/*.csv {args.dir}/")
        sys.exit(0)

    db = RealEstateDB(db_path=args.db_path)
    total_inserted = 0

    for idx, fpath in enumerate(csv_files, 1):
        fname = os.path.basename(fpath)
        print(f"\n[{idx}/{len(csv_files)}] 📄 '{fname}' 읽는 중...")
        try:
            raw_df = read_molit_csv(fpath)
            clean_df = parse_and_transform(raw_df, only_84=args.only_84, target_regions=None)
            
            if not clean_df.empty:
                total_cnt, inserted_cnt = db.upsert_transactions(clean_df)
                total_inserted += len(clean_df)
                print(f"    ✅ {len(clean_df):,}건 변환 완료 -> DB 적재 완료 (누적 {total_cnt:,}건)")
            else:
                print(f"    ⚠️ 조건에 맞는 데이터가 없습니다. (원자료: {len(raw_df):,}건)")
        except Exception as e:
            print(f"    ❌ 파일 처리 중 오류 발생: {e}")

    print("\n" + "=" * 70)
    print("🎉 [CSV 일괄 적재 완료!]")
    print(f"  - 총 반영 거래 건수: {total_inserted:,}건")
    print(f"  - DB 최종 총 레코드 수: {db.get_count():,}건")
    print("=" * 70)


if __name__ == "__main__":
    main()
