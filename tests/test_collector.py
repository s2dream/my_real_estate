import unittest
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from src.collector.collector import (
    generate_year_month_list,
    get_rolling_year_month_list,
    clean_and_filter,
    load_config,
    get_kst_now,
    get_retry_session,
)


class TestCollectorLogic(unittest.TestCase):
    """
    Collector 핵심 로직 단위 테스트 슈트:
    - 시작/종료 년월 리스트 생성
    - 롤링 버퍼 년월 리스트 엣지 케이스
    - 전용면적, 준공연도, 관심단지 복합 필터링
    - 모든 필터 비활성화 시 전건 보존
    - 빈 데이터프레임 처리
    - 설정 파일 부재 시 기본값 폴백 (load_config)
    - KST 타임존 오프셋 (+09:00)
    - 재시도 세션 (get_retry_session)
    """

    def test_generate_year_month_list(self):
        """연도 내/연도 간 년월 리스트 생성 검증"""
        # 동일 연도
        res_same_year = generate_year_month_list("202601", "202603")
        self.assertEqual(res_same_year, ["202601", "202602", "202603"])

        # 연도 경계 (Cross-year)
        res_cross_year = generate_year_month_list("202511", "202602")
        self.assertEqual(res_cross_year, ["202511", "202512", "202601", "202602"])

        # 시작이 종료보다 미래인 경우
        res_future = generate_year_month_list("202605", "202602")
        self.assertEqual(res_future, ["202602"])

    def test_rolling_year_month_list_buffer(self):
        """롤링 버퍼 개수 및 정렬 검증"""
        buf_0 = get_rolling_year_month_list(0)
        self.assertEqual(len(buf_0), 1)

        buf_3 = get_rolling_year_month_list(3)
        self.assertEqual(len(buf_3), 4)
        self.assertEqual(buf_3, sorted(buf_3))

    def test_clean_and_filter_complex_rules(self):
        """전용면적, 건축년도, 단지 필터 복합 조합 검증"""
        config = {
            "collection": {
                "area_filter": {
                    "enabled": True,
                    "types": [
                        {"name": "59타입", "min": 59.0, "max": 60.0},
                        {"name": "84타입", "min": 84.0, "max": 85.0},
                    ],
                },
                "build_year_filter": {
                    "enabled": True,
                    "within_years": 10,
                },
                "target_complexes": ["푸르지오", "자이"],
            }
        }

        raw_data = pd.DataFrame([
            # 통과 대상 1: 84타입, 2022년 준공, 푸르지오
            {
                "aptNm": "푸르지오",
                "dealYear": "2026",
                "dealMonth": "1",
                "dealDay": "10",
                "dealAmount": " 95,000 ",
                "excluUseAr": "84.9",
                "buildYear": "2022",
                "floor": "10",
            },
            # 통과 대상 2: 59타입, 2020년 준공, 자이
            {
                "aptNm": "자이",
                "dealYear": "2026",
                "dealMonth": "1",
                "dealDay": "12",
                "dealAmount": " 72,000 ",
                "excluUseAr": "59.8",
                "buildYear": "2020",
                "floor": "5",
            },
            # 탈락 대상 1: 면적 불일치 (74타입)
            {
                "aptNm": "푸르지오",
                "dealYear": "2026",
                "dealMonth": "1",
                "dealDay": "15",
                "dealAmount": " 80,000 ",
                "excluUseAr": "74.5",
                "buildYear": "2022",
                "floor": "7",
            },
            # 탈락 대상 2: 건축년도 초과 (2005년 준공, 10년 초과)
            {
                "aptNm": "자이",
                "dealYear": "2026",
                "dealMonth": "1",
                "dealDay": "16",
                "dealAmount": " 60,000 ",
                "excluUseAr": "84.5",
                "buildYear": "2005",
                "floor": "3",
            },
            # 탈락 대상 3: 미등록 단지 (래미안)
            {
                "aptNm": "래미안",
                "dealYear": "2026",
                "dealMonth": "1",
                "dealDay": "18",
                "dealAmount": " 90,000 ",
                "excluUseAr": "84.5",
                "buildYear": "2022",
                "floor": "12",
            },
        ])

        cleaned = clean_and_filter(raw_data, config)

        self.assertEqual(len(cleaned), 2, "통과 대상 2건만 남아야 합니다.")
        self.assertListEqual(sorted(cleaned["aptNm"].tolist()), ["자이", "푸르지오"])
        self.assertEqual(cleaned[cleaned["aptNm"] == "푸르지오"]["areaType"].iloc[0], "84타입")
        self.assertEqual(cleaned[cleaned["aptNm"] == "자이"]["areaType"].iloc[0], "59타입")
        self.assertEqual(cleaned[cleaned["aptNm"] == "푸르지오"]["dealAmount"].iloc[0], 95000)

    def test_clean_and_filter_disabled_filters(self):
        """필터가 모두 비활성화(enabled=False)된 경우 모든 데이터가 보존되는지 검증"""
        config = {
            "collection": {
                "area_filter": {"enabled": False},
                "build_year_filter": {"enabled": False},
                "target_complexes": [],
            }
        }
        raw_data = pd.DataFrame([
            {"aptNm": "단지A", "dealAmount": "50,000", "excluUseAr": "39.5", "buildYear": "1995", "dealYear": "2026", "dealMonth": "1", "dealDay": "1"},
            {"aptNm": "단지B", "dealAmount": "150,000", "excluUseAr": "114.2", "buildYear": "2000", "dealYear": "2026", "dealMonth": "1", "dealDay": "2"},
        ])
        cleaned = clean_and_filter(raw_data, config)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned.iloc[0]["dealAmount"], 50000)
        self.assertEqual(cleaned.iloc[1]["dealAmount"], 150000)

    def test_clean_and_filter_empty_dataframe(self):
        """빈 DataFrame 입력 시 에러 없이 빈 DataFrame 반환 검증"""
        cleaned = clean_and_filter(pd.DataFrame(), {})
        self.assertTrue(cleaned.empty)

    def test_load_config_fallback(self):
        """존재하지 않는 설정 파일 로드 시 기본 설정 반환 검증"""
        cfg = load_config("non_existent_config_path_xyz123.yml")
        self.assertIn("collection", cfg)
        self.assertIn("storage", cfg)
        self.assertEqual(cfg["collection"]["start_year_month"], "202601")
        self.assertEqual(cfg["storage"]["db_path"], "data/transactions.db")

    def test_get_kst_now_timezone(self):
        """한국 표준시(KST) 타임존 오프셋(+09:00) 검증"""
        kst = get_kst_now()
        self.assertIsNotNone(kst.tzinfo)
        utc_offset = kst.utcoffset()
        self.assertEqual(utc_offset, timedelta(hours=9))

    def test_get_retry_session(self):
        """재시도 세션 객체 생성 및 HTTPS 마운트 확인"""
        session = get_retry_session(retries=2, backoff_factor=0.1)
        self.assertIsInstance(session, requests.Session)
        self.assertIn("https://", session.adapters)
        self.assertIn("http://", session.adapters)


if __name__ == "__main__":
    unittest.main()
