import os
import sys
from dotenv import load_dotenv, find_dotenv
import pandas as pd
from collector import fetch_page, clean_and_filter, load_config

def test_single_api_call():
    """
    공공데이터포털 API 연동 최소 1회 호출 테스트 스크립트.
    일일 트래픽 한도(10,000회)를 아끼기 위해 오직 1회의 API 호출만 수행합니다.
    """
    print("=" * 60)
    print("🧪 [공공데이터포털 API 최소 호출 1회 테스트]")
    print("=" * 60)

    # 1. .env 로드
    load_dotenv(find_dotenv())
    api_key = os.environ.get("DATA_GO_KR_API_KEY", "").strip()

    if not api_key or api_key == "your_decoding_api_key_here":
        print("❌ [오류] .env 파일에 DATA_GO_KR_API_KEY 가 설정되지 않았습니다.")
        print("   .env 파일을 열고 발급받은 인증키를 입력해주세요.")
        sys.exit(1)

    masked_key = api_key[:6] + "*" * max(0, len(api_key) - 12) + api_key[-6:] if len(api_key) > 12 else "***"
    print(f"🔑 감지된 API Key: {masked_key} (길이: {len(api_key)})")

    # 2. 테스트용 단 1회 호출 (수원시 팔달구 41115, 최근 년월 기준 10건)
    lawd_cd = "41115"
    deal_ymd = "202401"
    num_of_rows = 10
    page_no = 1

    print(f"\n📡 API 단 1회 호출 진행 중... (지역: {lawd_cd}, 년월: {deal_ymd}, 요청: {num_of_rows}건)")
    items, total_count, code, msg = fetch_page(api_key, lawd_cd, deal_ymd, page_no=page_no, num_of_rows=num_of_rows)

    print(f"   - 응답 상태: Code '{code}', Msg '{msg}'")

    if code not in ["00", "000", "INFO-000"]:
        print(f"\n❌ [API 인증 대기 또는 키 오류 발생]")
        print("   1) 공공데이터포털에서 방금 API를 신청한 경우, 서버 동기화에 최대 1시간이 소요될 수 있습니다.")
        print("   2) 마이페이지 개발계정에서 '일반 인증키(Decoding)'를 복사하여 .env에 넣었는지 확인해주세요.")
        print("   3) 신청하신 오픈API 서비스명이 맞는지 확인해주세요.")
        print("\n📊 이번 테스트로 사용된 API 트래픽: 단 1회")
        return

    print("✅ 1. 공공데이터포털 API 연동 및 인증 성공!")
    print(f"   - 해당 월 전체 등록 거래: {total_count}건")
    print(f"   - 수신된 샘플 데이터: {len(items)}건")

    if not items:
        print("ℹ️ 데이터가 비어있습니다.")
        return

    # 3. 데이터 파싱 및 전처리 검증
    raw_df = pd.DataFrame(items)
    raw_df["regionName"] = "수원시 팔달구"
    sample_item = items[0]
    print(f"\n📋 수신 샘플 (첫 1건):")
    print(f"   - 단지명: {sample_item.get('aptNm')}")
    print(f"   - 거래금액: {sample_item.get('dealAmount')}만원")
    print(f"   - 전용면적: {sample_item.get('excluUseAr')}㎡ ({sample_item.get('floor')}층)")
    print(f"   - 거래일자: {sample_item.get('dealYear')}-{sample_item.get('dealMonth')}-{sample_item.get('dealDay')}")

    config = load_config("setting.yml")
    cleaned_df = clean_and_filter(raw_df.copy(), config)
    print(f"\n✨ setting.yml 전처리/필터 적용: 원본 {len(raw_df)}건 -> 필터 후 {len(cleaned_df)}건")

    print("\n" + "=" * 60)
    print("🎉 [테스트 성공] 모든 파이프라인 연동이 정상입니다!")
    print("📊 사용된 API 트래픽: 단 1회 (잔여 일일 트래픽: 9,999회)")
    print("=" * 60)

if __name__ == "__main__":
    test_single_api_call()
