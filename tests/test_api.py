import os
import sys
import unittest
from dotenv import load_dotenv, find_dotenv
import pandas as pd
from src.collector.collector import fetch_page, clean_and_filter, load_config


class TestApiIntegration(unittest.TestCase):
    """
    공공데이터포털 API 연동 최소 1회 호출 및 전처리 단위 테스트.
    """

    def test_clean_and_filter_logic(self):
        """데이터 전처리 및 필터링 로직 단위 테스트"""
        config = {
            "collection": {
                "area_filter": {"enabled": True, "types": [{"name": "84타입", "min": 84.0, "max": 85.0}]},
                "build_year_filter": {"enabled": False},
                "target_complexes": [],
            }
        }
        sample_df = pd.DataFrame([
            {"dealYear": "2026", "dealMonth": "1", "dealDay": "15", "dealAmount": " 85,000 ", "excluUseAr": "84.95", "aptNm": "테스트단지"},
            {"dealYear": "2026", "dealMonth": "1", "dealDay": "16", "dealAmount": " 55,000 ", "excluUseAr": "59.95", "aptNm": "테스트단지2"},
        ])
        cleaned = clean_and_filter(sample_df, config)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned.iloc[0]["dealAmount"], 85000)
        self.assertEqual(cleaned.iloc[0]["areaType"], "84타입")


def run_manual_api_test():
    """수동 API 단 1회 호출 테스트"""
    print("=" * 60)
    print("🧪 [공공데이터포털 API 최소 호출 1회 테스트]")
    print("=" * 60)

    load_dotenv(find_dotenv())
    api_key = os.environ.get("DATA_GO_KR_API_KEY", "").strip()

    if not api_key or api_key == "your_decoding_api_key_here":
        print("❌ [오류] .env 파일에 DATA_GO_KR_API_KEY 가 설정되지 않았습니다.")
        sys.exit(1)

    items, total_count, code, msg = fetch_page(api_key, "41115", "202401", page_no=1, num_of_rows=10)
    print(f"응답: Code '{code}', Msg '{msg}', Total {total_count}, Items {len(items)}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--manual":
        run_manual_api_test()
    else:
        unittest.main()
