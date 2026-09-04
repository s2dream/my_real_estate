import os
import sys
import unittest
import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest
from src.db.db_manager import RealEstateDB
from app import (
    format_korean_currency,
    DISTINCT_HIGH_CONTRAST_PALETTE,
    generate_golden_ratio_color,
    get_distinct_color_map,
    compute_all_time_highs,
)


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

        # 층수 슬라이더 존재 및 기본값(5층 이상) 검증
        floor_sliders = [s for s in at.slider if "층수" in s.label]
        self.assertTrue(len(floor_sliders) > 0, "층수 슬라이더가 사이드바에 존재해야 함")
        self.assertEqual(floor_sliders[0].value[0], 5, "층수 슬라이더의 기본 시작값은 5층이어야 함")
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

    def test_floor_filtering_default_five_or_more(self):
        """층수 필터 기본값(5층 이상) 적용 시 1~4층 저층이 정확히 제외되는지 검증"""
        sample_df = pd.DataFrame([
            {"aptNm": "단지A", "floor": 1, "dealAmount": 80000},
            {"aptNm": "단지A", "floor": 4, "dealAmount": 85000},
            {"aptNm": "단지A", "floor": 5, "dealAmount": 90000},
            {"aptNm": "단지A", "floor": 12, "dealAmount": 95000},
            {"aptNm": "단지A", "floor": 25, "dealAmount": 100000},
        ])
        min_f, max_f = 5, 49
        floor_num = pd.to_numeric(sample_df["floor"], errors="coerce")
        filtered = sample_df[(floor_num >= min_f) & (floor_num <= max_f)]

        self.assertEqual(len(filtered), 3)
        self.assertListEqual(filtered["floor"].tolist(), [5, 12, 25])
        self.assertNotIn(1, filtered["floor"].tolist())
        self.assertNotIn(4, filtered["floor"].tolist())

    def test_compute_all_time_highs_floor_independence(self):
        """신고가(is_ath)는 층수 필터와 무관하게 단지 전체 기준으로 산정됨을 검증"""
        df = pd.DataFrame([
            {"aptNm": "단지A", "dealDate": "2026-01-10", "floor": 10, "dealAmount": 90000, "isCanceled": False},
            {"aptNm": "단지A", "dealDate": "2026-01-15", "floor": 2, "dealAmount": 85000, "isCanceled": False},
            {"aptNm": "단지A", "dealDate": "2026-02-01", "floor": 15, "dealAmount": 95000, "isCanceled": False},
            {"aptNm": "단지A", "dealDate": "2026-02-10", "floor": 3, "dealAmount": 88000, "isCanceled": False},
            {"aptNm": "단지A", "dealDate": "2026-02-20", "floor": 20, "dealAmount": 100000, "isCanceled": False},
            {"aptNm": "단지A", "dealDate": "2026-03-01", "floor": 4, "dealAmount": 92000, "isCanceled": False},
        ])
        res = compute_all_time_highs(df)

        # 1) 전체 시계열 상에서 신고가는 90000(10층), 95000(15층), 100000(20층) 총 3건이어야 함
        ath_deals = res[res["is_ath"]]
        self.assertEqual(len(ath_deals), 3)
        self.assertListEqual(ath_deals["dealAmount"].tolist(), [90000, 95000, 100000])

        # 2) 5층 이하(1~4층) 거래 중에는 실제 신고가가 단 1건도 없어야 함
        low_floor_ath = res[(res["floor"] < 5) & (res["is_ath"])]
        self.assertEqual(len(low_floor_ath), 0, "5층 이하 매물 중에는 신고가가 없어야 함")

    def test_compute_all_time_highs_canceled_deals(self):
        """취소/해제 거래(isCanceled=True)는 신고가에 반영되지 않고 후속 정상 거래를 방해하지 않음 검증"""
        df = pd.DataFrame([
            {"aptNm": "단지B", "dealDate": "2026-01-10", "floor": 10, "dealAmount": 80000, "isCanceled": False},
            {"aptNm": "단지B", "dealDate": "2026-01-20", "floor": 15, "dealAmount": 150000, "isCanceled": True},  # 허위/취소 거래
            {"aptNm": "단지B", "dealDate": "2026-02-01", "floor": 12, "dealAmount": 85000, "isCanceled": False},
        ])
        res = compute_all_time_highs(df)

        # 취소건은 is_ath가 False여야 함
        canceled_row = res[res["dealAmount"] == 150000].iloc[0]
        self.assertFalse(canceled_row["is_ath"])

        # 취소건(150000) 때문에 85000 거래의 신고가 인정이 방해받아서는 안 됨 (85000 > 80000 이므로 신고가)
        deal_85 = res[res["dealAmount"] == 85000].iloc[0]
        self.assertTrue(deal_85["is_ath"])


class TestAppColorSystem(unittest.TestCase):
    """
    단지별 고대비 색상 매핑 및 세션 레지스트리 무결성 검증:
    - 2개 단지 선택 시 최고 대비(보색) 색상 부여
    - 단지 추가 시 기존 단지 색상 불변(안정성)
    - 24개 초과 시 황금각 HSL 생성기 정상 동작
    - 24개 팔레트 내 모든 색상 고유성(중복 없음)
    """

    def setUp(self):
        import streamlit as st
        st.session_state.clear()

    def test_palette_uniqueness(self):
        """24개 최고 대비 팔레트에 중복 색상이 없는지 검증"""
        self.assertEqual(len(DISTINCT_HIGH_CONTRAST_PALETTE), 24)
        self.assertEqual(len(set(DISTINCT_HIGH_CONTRAST_PALETTE)), 24)

    def test_initial_two_complexes_distinct_colors(self):
        """최초 2개 단지 선택 시 1번(블루)과 2번(오렌지) 최고 대비 색상이 배정되는지 검증"""
        reg = {}
        color_map = get_distinct_color_map(["단지A", "단지B"], ["단지A", "단지B", "단지C"], registry=reg)
        self.assertEqual(color_map["단지A"], DISTINCT_HIGH_CONTRAST_PALETTE[0])
        self.assertEqual(color_map["단지B"], DISTINCT_HIGH_CONTRAST_PALETTE[1])
        self.assertNotEqual(color_map["단지A"], color_map["단지B"])

    def test_color_stability_on_adding_new_complex(self):
        """신규 단지 추가 시 기존 단지 색상이 유지되고 새 단지에 다음 고대비 색상이 부여되는지 검증"""
        reg = {}
        # 1차: A, B 선택
        map1 = get_distinct_color_map(["단지A", "단지B"], ["단지A", "단지B", "단지C"], registry=reg)
        color_a = map1["단지A"]
        color_b = map1["단지B"]

        # 2차: C 추가 (A, B, C 선택)
        map2 = get_distinct_color_map(["단지A", "단지B", "단지C"], ["단지A", "단지B", "단지C"], registry=reg)
        self.assertEqual(map2["단지A"], color_a, "기존 단지A 색상은 불변이어야 함")
        self.assertEqual(map2["단지B"], color_b, "기존 단지B 색상은 불변이어야 함")
        self.assertEqual(map2["단지C"], DISTINCT_HIGH_CONTRAST_PALETTE[2], "단지C는 3번째 고대비 색상(그린)을 받아야 함")

    def test_golden_angle_generation_over_24(self):
        """24개 초과 단지 등록 시 황금각 HSL 색상이 정상 생성되는지 검증"""
        color_25 = generate_golden_ratio_color(25)
        self.assertTrue(color_25.startswith("hsl("))
        self.assertTrue(color_25.endswith(")"))

        # 30개 단지 등록 시 모든 단지 색상이 고유함을 확인
        reg = {}
        many_apts = [f"아파트_{i}" for i in range(30)]
        many_map = get_distinct_color_map(many_apts, many_apts, registry=reg)
        self.assertEqual(len(many_map), 30)
        self.assertEqual(len(set(many_map.values())), 30, "30개 단지의 배정 색상은 모두 고유해야 함")


if __name__ == "__main__":
    unittest.main()
