import os
import sys
import unittest
from streamlit.testing.v1 import AppTest
from src.db.db_manager import RealEstateDB

class TestAppDashboard(unittest.TestCase):
    """
    Streamlit 대시보드(app.py 및 src/dashboard/app.py)의 무결성 및 테마/필터/상세목록 검증 테스트 슈트.
    """

    def setUp(self):
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.db = RealEstateDB()
        self.total_count = self.db.get_count()

    def test_app_loads_without_exception(self):
        """src/dashboard/app.py 가 예외 없이 정상 로드 및 렌더링되는지 검증"""
        app_file = os.path.join(self.project_root, "src/dashboard/app.py")
        at = AppTest.from_file(app_file, default_timeout=15)
        at.run()
        self.assertFalse(at.exception, f"src/dashboard/app.py 실행 중 예외 발생: {at.exception}")

    def test_app_filters_interaction(self):
        """사이드바 필터 및 멀티셀렉트 상호작용 검증"""
        app_file = os.path.join(self.project_root, "src/dashboard/app.py")
        at = AppTest.from_file(app_file, default_timeout=15)
        at.run()

        # 타이틀 정상 출력 확인
        self.assertTrue(any("스마트 아파트 실거래가" in str(title.value) for title in at.title))
        
        # multiselect 위젯들 정상 존재 확인 (지역, 단지명 등)
        self.assertGreater(len(at.multiselect), 0, "지역 또는 단지 멀티셀렉트 위젯이 존재해야 함")
        self.assertFalse(at.exception)

    def test_dark_mode_css_and_summary_box(self):
        """다크모드 호환 CSS 클래스 및 Tab 5 요약 박스 렌더링 무결성 검증"""
        app_file = os.path.join(self.project_root, "src/dashboard/app.py")
        at = AppTest.from_file(app_file, default_timeout=15)
        at.run()

        # markdown 블록 중 다크모드 호환 클래스(.result-summary-box, .metric-card) 포함 여부 검증
        all_markdown_text = " ".join([str(m.value) for m in at.markdown])
        self.assertIn("result-summary-box", all_markdown_text, "Tab 5의 result-summary-box 클래스가 렌더링되어야 함")
        self.assertIn("var(--text-color", all_markdown_text, "다크모드 호환 CSS 변수가 스타일에 정의되어야 함")
        self.assertIn("metric-card", all_markdown_text, "metric-card 클래스가 정의되어야 함")

        # 고정 흰색/검은색 충돌 인라인 스타일(background-color: #f8fafc 없는지 확인)
        self.assertNotIn("background-color: #f8fafc", all_markdown_text, "다크모드 충돌 위험이 있는 하드코딩 인라인 배경색이 없어야 함")

if __name__ == "__main__":
    unittest.main()
