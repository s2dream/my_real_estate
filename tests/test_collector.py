import unittest
import pandas as pd
from datetime import datetime, timezone, timedelta
from src.collector.collector import (
    generate_year_month_list,
    get_rolling_year_month_list,
    clean_and_filter,
)


class TestCollectorLogic(unittest.TestCase):
    """
    Collector 핵심 로직 단위 테스트 슈트:
    - 시작/종료 년월 리스트 생성
    - 롤링 버퍼 년월 리스트 엣지 케이스
    - 전용면적, 준공연도, 관심단지 복합 필터링
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

        # 테스트 케이스 데이터
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


if __name__ == "__main__":
    unittest.main()
