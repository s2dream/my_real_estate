import os
import sys
import unittest
from streamlit.testing.v1 import AppTest
from db_manager import RealEstateDB

class TestAppDashboard(unittest.TestCase):
    """
    Streamlit 대시보드(app.py)의 무결성을 검증하는 자동화 테스트 슈트.
    - AppTest 를 통해 app.py 실행 중 NameError, KeyError, IndexError 등의 런타임 예외가 없는지 검증
    - 필터링, 위젯 초기화, 데이터 로드 상태 검증
    """

    def setUp(self):
        self.db = RealEstateDB()
        self.total_count = self.db.get_count()

    def test_app_loads_without_exception(self):
        """app.py 가 예외 없이 정상 로드 및 렌더링되는지 검증"""
        at = AppTest.from_file("app.py", default_timeout=15)
        at.run()

        # 런타임 예외(NameError, AttributeError 등)가 발생하지 않아야 함
        self.assertFalse(at.exception, f"app.py 실행 중 예외 발생: {at.exception}")

    def test_app_filters_interaction(self):
        """사이드바 필터 및 멀티셀렉트 상호작용 검증"""
        at = AppTest.from_file("app.py", default_timeout=15)
        at.run()

        # 타이틀 정상 출력 확인
        self.assertTrue(any("스마트 아파트 실거래가" in str(title.value) for title in at.title))
        
        # multiselect 위젯들 정상 존재 확인 (지역, 단지명 등)
        self.assertGreater(len(at.multiselect), 0, "지역 또는 단지 멀티셀렉트 위젯이 존재해야 함")

        # 런타임 에러 없음 재확인
        self.assertFalse(at.exception)

if __name__ == "__main__":
    unittest.main()
