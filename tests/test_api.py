import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from dotenv import load_dotenv, find_dotenv
import pandas as pd
import requests
from src.collector.collector import (
    fetch_page,
    fetch_all_pages_for_month,
    clean_and_filter,
)


class TestApiMockUnit(unittest.TestCase):
    """
    공공데이터포털 API 연동 단위 테스트 슈트 (Mock 기반 네트워크 격리):
    - 단일 / 복수 아이템 XML 응답 파싱
    - 0건 데이터 처리
    - 게이트웨이 인증 에러 (OpenAPI_ServiceResponse)
    - API 결과 에러 코드 (resultCode != 00)
    - 네트워크 장애 및 타임아웃 예외
    - 다중 페이지 순회 수집 (Pagination)
    """

    def setUp(self):
        self.api_key = "test_decoding_key"
        self.lawd_cd = "41115"
        self.deal_ymd = "202601"

    @patch("src.collector.collector.get_retry_session")
    def test_fetch_page_single_item(self, mock_get_session):
        """단일 아이템(XML 객체가 dict) 응답 정상 파싱 및 리스트 변환 검증"""
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
        <response>
            <header>
                <resultCode>00</resultCode>
                <resultMsg>NORMAL SERVICE.</resultMsg>
            </header>
            <body>
                <items>
                    <item>
                        <aptNm>매교역푸르지오SKVIEW</aptNm>
                        <dealAmount>95,000</dealAmount>
                        <dealYear>2026</dealYear>
                        <dealMonth>1</dealMonth>
                        <dealDay>15</dealDay>
                    </item>
                </items>
                <totalCount>1</totalCount>
            </body>
        </response>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = xml_text
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        items, total_count, code, msg = fetch_page(self.api_key, self.lawd_cd, self.deal_ymd, page_no=1)

        self.assertEqual(code, "00")
        self.assertEqual(total_count, 1)
        self.assertIsInstance(items, list)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["aptNm"], "매교역푸르지오SKVIEW")

    @patch("src.collector.collector.get_retry_session")
    def test_fetch_page_multiple_items(self, mock_get_session):
        """복수 아이템(XML 객체가 list) 응답 정상 파싱 검증"""
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
        <response>
            <header>
                <resultCode>000</resultCode>
                <resultMsg>NORMAL SERVICE.</resultMsg>
            </header>
            <body>
                <items>
                    <item>
                        <aptNm>단지1</aptNm>
                        <dealAmount>80,000</dealAmount>
                    </item>
                    <item>
                        <aptNm>단지2</aptNm>
                        <dealAmount>90,000</dealAmount>
                    </item>
                </items>
                <totalCount>2</totalCount>
            </body>
        </response>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = xml_text
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        items, total_count, code, msg = fetch_page(self.api_key, self.lawd_cd, self.deal_ymd, page_no=1)

        self.assertEqual(code, "000")
        self.assertEqual(total_count, 2)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1]["aptNm"], "단지2")

    @patch("src.collector.collector.get_retry_session")
    def test_fetch_page_zero_items(self, mock_get_session):
        """거래 내역이 0건인 정상 응답 처리 검증"""
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
        <response>
            <header>
                <resultCode>00</resultCode>
                <resultMsg>NORMAL SERVICE.</resultMsg>
            </header>
            <body>
                <items/>
                <totalCount>0</totalCount>
            </body>
        </response>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = xml_text
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        items, total_count, code, msg = fetch_page(self.api_key, self.lawd_cd, self.deal_ymd)

        self.assertEqual(code, "00")
        self.assertEqual(total_count, 0)
        self.assertEqual(items, [])

    @patch("src.collector.collector.get_retry_session")
    def test_fetch_page_gateway_auth_error(self, mock_get_session):
        """공공데이터포털 게이트웨이 인증 실패(OpenAPI_ServiceResponse) 방어 검증"""
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
        <OpenAPI_ServiceResponse>
            <cmmMsgHeader>
                <errMsg>SERVICE ERROR</errMsg>
                <returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
                <returnReasonCode>30</returnReasonCode>
            </cmmMsgHeader>
        </OpenAPI_ServiceResponse>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = xml_text
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        items, total_count, code, msg = fetch_page(self.api_key, self.lawd_cd, self.deal_ymd)

        self.assertEqual(code, "AUTH_ERROR")
        self.assertEqual(items, [])
        self.assertIn("SERVICE ERROR", msg)

    @patch("src.collector.collector.get_retry_session")
    def test_fetch_page_api_error_code(self, mock_get_session):
        """API 결과 코드가 비정상(00 아님)일 때의 처리 검증"""
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
        <response>
            <header>
                <resultCode>99</resultCode>
                <resultMsg>INVALID_REQUEST_PARAMETER_ERROR</resultMsg>
            </header>
            <body/>
        </response>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = xml_text
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_get_session.return_value = mock_session

        items, total_count, code, msg = fetch_page(self.api_key, self.lawd_cd, self.deal_ymd)

        self.assertEqual(code, "99")
        self.assertEqual(items, [])
        self.assertEqual(msg, "INVALID_REQUEST_PARAMETER_ERROR")

    @patch("src.collector.collector.get_retry_session")
    def test_fetch_page_network_exception(self, mock_get_session):
        """네트워크 타임아웃/연결 오류 발생 시 크래시 없이 에러 반환 검증"""
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.exceptions.Timeout("Connection timed out")
        mock_get_session.return_value = mock_session

        items, total_count, code, msg = fetch_page(self.api_key, self.lawd_cd, self.deal_ymd)

        self.assertEqual(code, "ERROR")
        self.assertEqual(total_count, 0)
        self.assertEqual(items, [])
        self.assertIn("Connection timed out", msg)

    @patch("src.collector.collector.fetch_page")
    def test_fetch_all_pages_multi_page(self, mock_fetch_page):
        """총 건수가 1000건을 초과할 때 복수 페이지 순회 수집 검증"""
        # 1페이지 호출: totalCount=1500, items 1개 반환
        # 2페이지 호출: items 1개 반환
        mock_fetch_page.side_effect = [
            ([{"aptNm": "1페이지 단지"}], 1500, "00", "OK"),
            ([{"aptNm": "2페이지 단지"}], 1500, "00", "OK"),
        ]

        results = fetch_all_pages_for_month(self.api_key, self.lawd_cd, "수원시 팔달구", self.deal_ymd)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["aptNm"], "1페이지 단지")
        self.assertEqual(results[1]["aptNm"], "2페이지 단지")
        self.assertEqual(mock_fetch_page.call_count, 2)

    @patch("src.collector.collector.fetch_page")
    def test_fetch_all_pages_error_first_page(self, mock_fetch_page):
        """첫 페이지 호출 실패 시 빈 리스트 반환 검증"""
        mock_fetch_page.return_value = ([], 0, "AUTH_ERROR", "인증 에러")

        results = fetch_all_pages_for_month(self.api_key, self.lawd_cd, "수원시 팔달구", self.deal_ymd)

        self.assertEqual(results, [])
        self.assertEqual(mock_fetch_page.call_count, 1)


class TestApiDataFilter(unittest.TestCase):
    """전처리 및 필터링 로직 검증"""

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
