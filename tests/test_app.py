import os
import sys
import unittest
import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest
from src.db.db_manager import RealEstateDB
from app import format_korean_currency


class TestAppDashboard(unittest.TestCase):
    """
    Streamlit 대시보드(app.py) 무결성 및 테마/필터/상세목록 검증 테스트 슈트.
    """

    def setUp(self):
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.db = RealEstateDB()
        self.total_count = self.db.get_count()

    def test_app_loads_without_exception(self):
        """루트 app.py 가 예외 없이 정상 로드 및 렌더링되는지 검증"""
        app_file = os.path.join(self.project_root, "app.py")
        at = AppTest.from_file(app_file, default_timeout=30)
        at.run()
        self.assertFalse(at.exception, f"app.py 실행 중 예외 발생: {at.exception}")

    def test_app_filters_interaction(self):
        """사이드바 필터 및 멀티셀렉트 상호작용 검증"""
        app_file = os.path.join(self.project_root, "app.py")
        at = AppTest.from_file(app_file, default_timeout=30)
        at.run()

        # 타이틀 정상 출력 확인
        self.assertTrue(any("스마트 아파트 실거래가" in str(title.value) for title in at.title))
        
        # multiselect 위젯들 정상 존재 확인 (지역, 단지명 등)
        self.assertGreater(len(at.multiselect), 0, "지역 또는 단지 멀티셀렉트 위젯이 존재해야 함")
        self.assertFalse(at.exception)

    def test_dark_mode_css_and_summary_box(self):
        """다크모드 호환 CSS 클래스 및 Tab 5 요약 박스 렌더링 무결성 검증"""
        app_file = os.path.join(self.project_root, "app.py")
        at = AppTest.from_file(app_file, default_timeout=30)
        at.run()

        all_markdown_text = " ".join([str(m.value) for m in at.markdown])
        self.assertIn("result-summary-box", all_markdown_text, "Tab 5의 result-summary-box 클래스가 렌더링되어야 함")
        self.assertIn("var(--text-color", all_markdown_text, "다크모드 호환 CSS 변수가 스타일에 정의되어야 함")
        self.assertIn("metric-card", all_markdown_text, "metric-card 클래스가 정의되어야 함")
        self.assertNotIn("background-color: #f8fafc", all_markdown_text, "다크모드 충돌 위험이 있는 하드코딩 인라인 배경색이 없어야 함")


class TestAppHelperFunctions(unittest.TestCase):
    """
    대시보드 핵심 비즈니스 로직 및 파생변수 연산 단위 테스트:
    - 한글 통화 포맷팅 (format_korean_currency)
    - 층수 그룹 분류 (floorGroup)
    - 평당가 계산 및 취소 여부 판별
    """

    def test_format_korean_currency(self):
        """만원 단위 숫자를 'X억 Y,YYY만원' 포맷으로 정확히 변환하는지 검증"""
        # 결측치 및 0
        self.assertEqual(format_korean_currency(0), "-")
        self.assertEqual(format_korean_currency(None), "-")
        self.assertEqual(format_korean_currency(np.nan), "-")

        # 억 + 만원 조합
        self.assertEqual(format_korean_currency(85000), "8억 5,000만원")
        self.assertEqual(format_korean_currency(123450), "12억 3,450만원")

        # 억 단위 딱 떨어지는 경우
        self.assertEqual(format_korean_currency(10000), "1억원")
        self.assertEqual(format_korean_currency(100000), "10억원")

        # 1억 미만 만원 단위
        self.assertEqual(format_korean_currency(9500), "9,500만원")
        self.assertEqual(format_korean_currency(500), "500만원")

    def test_floor_group_categorization(self):
        """층수별 그룹핑 로직(저층, 중층, 고층, 미분류) 검증"""
        def categorize_floor(fl):
            if pd.isna(fl):
                return "미분류"
            fl = int(fl)
            if fl <= 5:
                return "1) 저층 (1~5층)"
            elif fl <= 15:
                return "2) 중층 (6~15층)"
            else:
                return "3) 고층/로열 (16층+)"

        self.assertEqual(categorize_floor(1), "1) 저층 (1~5층)")
        self.assertEqual(categorize_floor(5), "1) 저층 (1~5층)")
        self.assertEqual(categorize_floor(6), "2) 중층 (6~15층)")
        self.assertEqual(categorize_floor(15), "2) 중층 (6~15층)")
        self.assertEqual(categorize_floor(16), "3) 고층/로열 (16층+)")
        self.assertEqual(categorize_floor(30), "3) 고층/로열 (16층+)")
        self.assertEqual(categorize_floor(np.nan), "미분류")

    def test_derived_variables_calculation(self):
        """평당가 계산 및 취소 거래 플래그 판별 검증"""
        df = pd.DataFrame([
            {"dealAmount": 100000, "excluUseAr": 84.95, "cdealType": "O"},
            {"dealAmount": 60000, "excluUseAr": 59.95, "cdealType": None},
            {"dealAmount": 80000, "excluUseAr": 84.00, "cdealType": "취소"},
        ])

        # 평당가 계산
        pyeong = df["excluUseAr"] / 3.305785
        df["pyeongPrice"] = (df["dealAmount"] / pyeong).round(1)

        # 84.95㎡ = 약 25.697평, 100000 / 25.697 = 약 3891.4 만원/평
        expected_pyeong_price_0 = round(100000 / (84.95 / 3.305785), 1)
        self.assertEqual(df.iloc[0]["pyeongPrice"], expected_pyeong_price_0)

        # 취소 여부 불리언
        df["isCanceled"] = df["cdealType"].isin(["O", "0", "취소", "해제"])
        self.assertTrue(df.iloc[0]["isCanceled"])
        self.assertFalse(df.iloc[1]["isCanceled"])
        self.assertTrue(df.iloc[2]["isCanceled"])


if __name__ == "__main__":
    unittest.main()
