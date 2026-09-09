import os
import sys
import tempfile
from pathlib import Path
import streamlit as st
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest
from src.db.db_manager import RealEstateDB
from app import (
    load_data,
    format_korean_currency,
    DISTINCT_HIGH_CONTRAST_PALETTE,
    generate_golden_ratio_color,
    get_distinct_color_map,
    compute_all_time_highs,
    compute_daily_moving_average,
    compute_monthly_prices,
)


class TestAppDashboard(unittest.TestCase):
    """
    Streamlit 대시보드(app.py) 무결성 및 테마/필터/상세목록 검증 테스트 슈트.
    """

    def setUp(self):
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = str(Path(self.temp_dir.name) / "transactions.db")
        self.db = RealEstateDB(self.db_path)
        rows = []
        for day, price, floor, canceled in [
            ("2025-12-31", 200000, 2, None),
            ("2026-01-01", 80000, 10, None),
            ("2026-01-07", 100000, 10, None),
            ("2026-01-08", 120000, 20, None),
            ("2026-02-01", 110000, 10, None),
            ("2026-02-07", 120000, 10, None),
            ("2026-02-08", 130000, 20, None),
            ("2026-02-09", 900000, 20, "O"),
        ]:
            rows.append(dict(dealDate=day, dealYear=day[:4], dealMonth=day[5:7],
                             dealDay=day[8:], sggCd="41115", regionName="수원시 팔달구",
                             umdNm="매교동", jibun="1", aptNm="매교역푸르지오SKVIEW",
                             floor=floor, excluUseAr=84.9, areaType="84타입",
                             dealAmount=price, buildYear="2022", dealType="중개거래",
                             cdealType=canceled))
        self.db.upsert_transactions(pd.DataFrame(rows))
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)

    def make_app(self):
        # 실제 app.py와 DB 로더를 실행하되 운영 DB·설정·현재 시각에는 의존하지 않는다.
        script = f"""
import runpy
from datetime import datetime, timezone
namespace = runpy.run_path({str(Path(self.project_root) / 'app.py')!r})
class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = datetime(2026, 12, 31, 15, 30, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
context = namespace['main'].__globals__
context['datetime'] = FixedDateTime
context['load_setting'] = lambda: {{'storage': {{'db_path': {self.db_path!r}}}}}
namespace['main']()
"""
        return AppTest.from_string(script, default_timeout=30)

    def test_app_loads_without_exception(self):
        """루트 app.py 가 예외 없이 정상 로드 및 렌더링되는지 검증"""
        at = self.make_app()
        at.run()
        self.assertFalse(at.exception, f"app.py 실행 중 예외 발생: {at.exception}")

    def test_floor_change_preserves_contract_period(self):
        from datetime import date
        at = self.make_app().run()
        period = (date(2026, 1, 7), date(2026, 2, 8))
        at.date_input[0].set_value(period).run()
        next(s for s in at.slider if "층수 범위" in s.label).set_value((20, 20)).run()
        self.assertFalse(at.exception)
        self.assertEqual(at.date_input[0].value, period)
        detail = next(d.value for d in at.dataframe if "계약일" in d.value.columns)
        self.assertEqual(len(detail), 2)

    def test_age_change_preserves_valid_complex_and_empty_selection(self):
        row = dict(dealDate="2026-01-15", dealYear="2026", dealMonth="01",
                   dealDay="15", sggCd="41115", regionName="수원시 팔달구",
                   umdNm="매교동", jibun="2", aptNm="추가단지", floor=10,
                   excluUseAr=84.9, areaType="84타입", dealAmount=70000,
                   buildYear="2020", dealType="중개거래")
        self.db.upsert_transactions(pd.DataFrame([row]))
        at = self.make_app().run()
        age = lambda: next(s for s in at.slider if "연식 기준" in s.label)
        complexes = lambda: next(m for m in at.multiselect if m.label == "아파트 단지명")
        age().set_value(10).run()
        complexes().set_value(["추가단지"]).run()
        age().set_value(5).run()
        self.assertEqual(complexes().value, [])  # 제외된 선택을 기본 단지로 바꾸지 않는다.
        age().set_value(10).run()
        self.assertEqual(complexes().value, [])  # 명시적인 전체 단지 선택을 유지한다.
        complexes().set_value(["매교역푸르지오SKVIEW", "추가단지"]).run()
        age().set_value(5).run()
        self.assertEqual(complexes().value, ["매교역푸르지오SKVIEW"])
        self.assertFalse(at.exception)

    def test_empty_results_preserve_analysis_options(self):
        at = self.make_app().run()
        next(c for c in at.checkbox if c.label == "단지별 7일 이동평균 추세선").uncheck().run()
        at.text_input(key="tab5_keyword_search").set_value("매교동").run()
        regions = lambda: next(m for m in at.multiselect if m.label == "지역 선택")
        regions().set_value([]).run()
        self.assertFalse(at.exception)
        regions().set_value(["수원시 팔달구"]).run()
        self.assertFalse(at.exception)
        self.assertFalse(next(c for c in at.checkbox if c.label == "단지별 7일 이동평균 추세선").value)
        self.assertEqual(at.text_input(key="tab5_keyword_search").value, "매교동")

    def test_app_filters_interaction(self):
        """사이드바 필터 및 멀티셀렉트 상호작용 검증"""
        at = self.make_app()
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
        at = self.make_app()
        at.run()

        all_markdown_text = " ".join([str(m.value) for m in at.markdown])
        self.assertIn("result-summary-box", all_markdown_text, "Tab 5의 result-summary-box 클래스가 렌더링되어야 함")
        self.assertIn("var(--text-color", all_markdown_text, "다크모드 호환 CSS 변수가 스타일에 정의되어야 함")
        self.assertIn("metric-card", all_markdown_text, "metric-card 클래스가 정의되어야 함")
        self.assertNotIn("background-color: #f8fafc", all_markdown_text, "다크모드 충돌 위험이 있는 하드코딩 인라인 배경색이 없어야 함")

    def test_kst_new_year_changes_build_year_cutoff(self):
        at = self.make_app().run()
        self.assertFalse(at.exception)
        captions = " ".join(str(c.value) for c in at.caption)
        self.assertIn("2022년 이후 준공", captions)  # UTC는 2026년, KST는 2027년

    def test_monthly_change_uses_medians_and_excludes_canceled(self):
        at = self.make_app().run()
        self.assertFalse(at.exception)
        card = next(m.value for m in at.markdown if "첫·마지막 관측월" in m.value)
        self.assertIn("+20.0%", card)  # 최초/최종 개별 거래 비교는 +62.5%
        self.assertIn("2026-01", card)
        self.assertIn("2026-02", card)
        self.assertEqual(card.count("(3건)"), 2)
        monthly = next(d.value for d in at.dataframe if "중위가격(만원)" in d.value.columns)
        self.assertEqual(monthly["중위가격(만원)"].tolist(), [100000, 120000])
        self.assertEqual(monthly["거래건수"].tolist(), [3, 3])

    def test_one_month_selection_disables_change_rate(self):
        from datetime import date
        at = self.make_app().run()
        at.date_input[0].set_value((date(2026, 1, 1), date(2026, 1, 31))).run()
        self.assertFalse(at.exception)
        card = next(m.value for m in at.markdown if "첫·마지막 관측월" in m.value)
        self.assertIn("비교 불가 (1개월)", card)
        self.assertNotIn("+0.0%", card)

    def test_single_floor_selection_skips_regression(self):
        at = self.make_app().run()
        next(s for s in at.slider if "층수 범위" in s.label).set_value((10, 10)).run()
        self.assertFalse(at.exception)
        self.assertTrue(any("서로 다른 층수" in m.value for m in at.info))
        self.assertFalse(any("회귀 기울기(관측값)" in d.value.columns for d in at.dataframe))

    def test_regression_reports_sample_count_and_observed_statistics(self):
        at = self.make_app().run()
        self.assertFalse(at.exception)
        regression = next(d.value for d in at.dataframe if "회귀 기울기(관측값)" in d.value.columns)
        self.assertEqual(regression.iloc[0]["거래건수"], 6)
        self.assertEqual(regression.iloc[0]["회귀 기울기(관측값)"], "+2,250 만원/층")
        self.assertIn("결정계수 (R²)", regression.columns)
        self.assertTrue(any("인과 효과를 뜻하지 않습니다" in c.value for c in at.caption))

    def test_empty_region_selection_shows_no_results(self):
        at = self.make_app().run()
        next(m for m in at.multiselect if m.label == "지역 선택").set_value([]).run()
        self.assertFalse(at.exception)
        self.assertTrue(any("해당하는 실거래 데이터가 없습니다" in m.value for m in at.info))

    def test_highs_keep_history_before_date_and_floor_filters(self):
        at = self.make_app().run()
        self.assertFalse(at.exception)
        card = next(m.value for m in at.markdown if "첫·마지막 관측월" in m.value)
        self.assertIn("수집 기간 내 신고가 갱신 0회", card)
        self.assertTrue(any("2025-12-31" in c.value and "신고가 비교 범위" in c.value for c in at.caption))



class TestAppHelperFunctions(unittest.TestCase):
    """
    대시보드 핵심 비즈니스 로직 및 파생변수 연산 단위 테스트:
    - 한글 통화 포맷팅 (format_korean_currency)
    - 층수 그룹 분류 (floorGroup)
    - 평당가 계산 및 취소 여부 판별
    """

    def test_calendar_week_excludes_old_trades(self):
        df = pd.DataFrame({
            "dealDate": pd.to_datetime(["2026-01-01", "2026-01-07", "2026-01-08", "2026-02-01"]),
            "dealAmount": [100, 200, 400, 900],
        })
        result = compute_daily_moving_average(df)
        self.assertEqual(result["MA7"].tolist(), [100, 150, 300, 900])

    def test_calendar_week_weights_each_observed_day_equally(self):
        df = pd.DataFrame({
            "dealDate": pd.to_datetime(["2026-01-02", "2026-01-01", "2026-01-01"]),
            "dealAmount": [400, 100, 300],
        })
        result = compute_daily_moving_average(df)
        self.assertEqual(result["MA7"].tolist(), [200, 300])

    def test_monthly_median_counts_and_missing_month(self):
        df = pd.DataFrame({
            "dealDate": pd.to_datetime(["2026-03-01", "2026-01-01", "2026-01-02", "2026-01-03"]),
            "dealAmount": [300, 100, 200, 900],
        })
        result = compute_monthly_prices(df)
        self.assertEqual(result["계약월"].tolist(), ["2026-01", "2026-03"])
        self.assertEqual(result["중위가격"].tolist(), [200, 300])
        self.assertEqual(result["거래건수"].tolist(), [3, 1])

    def test_price_aggregations_ignore_missing_values_and_preserve_input(self):
        df = pd.DataFrame({
            "dealDate": pd.to_datetime(["2025-12-31", "2026-01-01", None, "2026-01-02"]),
            "dealAmount": [100, 300, 9999, np.nan],
        })
        original = df.copy(deep=True)
        daily = compute_daily_moving_average(df)
        monthly = compute_monthly_prices(df)
        self.assertEqual(daily["MA7"].tolist(), [100, 200])
        self.assertEqual(monthly["계약월"].tolist(), ["2025-12", "2026-01"])
        self.assertEqual(monthly["거래건수"].tolist(), [1, 1])
        pd.testing.assert_frame_equal(df, original)

    def test_price_aggregations_handle_empty_or_invalid_data(self):
        for df in [
            pd.DataFrame({"dealDate": pd.to_datetime([]), "dealAmount": pd.Series(dtype=float)}),
            pd.DataFrame({"dealDate": pd.to_datetime([None]), "dealAmount": [100]}),
        ]:
            with self.subTest(data=df.to_dict()):
                self.assertTrue(compute_daily_moving_average(df).empty)
                self.assertTrue(compute_monthly_prices(df).empty)

    def test_monthly_median_with_even_number_of_transactions(self):
        df = pd.DataFrame({
            "dealDate": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "dealAmount": [100, 400],
        })
        self.assertEqual(compute_monthly_prices(df).iloc[0]["중위가격"], 250)

    def test_highs_are_separate_by_complex_and_area_and_ignore_ties(self):
        df = pd.DataFrame([
            {"aptNm": "A", "areaType": "84", "dealDate": "2026-01-03", "dealAmount": 110},
            {"aptNm": "A", "areaType": "84", "dealDate": "2026-01-01", "dealAmount": 100},
            {"aptNm": "A", "areaType": "84", "dealDate": "2026-01-02", "dealAmount": 100},
            {"aptNm": "A", "areaType": "59", "dealDate": "2026-01-02", "dealAmount": 50},
            {"aptNm": "B", "areaType": "84", "dealDate": "2026-01-02", "dealAmount": 70},
        ], index=[11, 22, 33, 44, 55])
        original = df.copy(deep=True)
        result = compute_all_time_highs(df)
        self.assertEqual(result["is_ath"].tolist(), [True, True, False, True, True])
        self.assertEqual(result.index.tolist(), df.index.tolist())
        pd.testing.assert_frame_equal(df, original)

    def test_highs_with_only_canceled_transactions(self):
        df = pd.DataFrame({"aptNm": ["A", "A"], "dealAmount": [100, 200],
                           "dealDate": pd.to_datetime(["2026-01-01", "2026-01-02"]),
                           "isCanceled": [True, True]})
        self.assertFalse(compute_all_time_highs(df)["is_ath"].any())

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

    def load_fixture(self, frame):
        # DB 접근만 대체하고 실제 load_data의 파생변수 계산을 검증한다.
        with patch("app.load_setting", return_value={}), \
             patch("app.os.path.exists", return_value=True), \
             patch("app.RealEstateDB") as database:
            database.return_value.get_all_transactions.return_value = frame.copy()
            return load_data.__wrapped__()

    def test_floor_group_categorization(self):
        floors = [1, 5, 6, 15, 16, 30, np.nan]
        result = self.load_fixture(pd.DataFrame({"floor": floors}))
        self.assertIsNotNone(result)
        self.assertEqual(result["floorGroup"].tolist(), [
            "1) 저층 (1~5층)", "1) 저층 (1~5층)",
            "2) 중층 (6~15층)", "2) 중층 (6~15층)",
            "3) 고층/로열 (16층+)", "3) 고층/로열 (16층+)", "미분류",
        ])

    def test_derived_variables_calculation(self):
        result = self.load_fixture(pd.DataFrame([
            {"dealAmount": 100000, "excluUseAr": 84.95, "cdealType": "O"},
            {"dealAmount": 60000, "excluUseAr": 59.95, "cdealType": None},
            {"dealAmount": 80000, "excluUseAr": 84.00, "cdealType": "취소"},
        ]))
        self.assertIsNotNone(result)
        self.assertEqual(result["pyeongPrice"].tolist(), [3891.4, 3308.5, 3148.4])
        self.assertEqual(result["isCanceled"].tolist(), [True, False, True])

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
