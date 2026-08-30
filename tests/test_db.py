import os
import unittest
import pandas as pd
from src.db.db_manager import RealEstateDB


class TestRealEstateDB(unittest.TestCase):
    """
    RealEstateDB 단위 테스트 슈트:
    - 인메모리 스키마 초기화
    - 멱등성(UPSERT 중복 방지)
    - 취소 거래(cdealType/cdealDay) 갱신
    - 지역별 통계 쿼리
    """

    def setUp(self):
        # 테스트 격리를 위해 인메모리 DB 사용
        self.db = RealEstateDB(db_path=":memory:")

    def test_init_db_schema(self):
        """테이블 스키마 및 인덱스 정상 생성 확인"""
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transactions'")
            self.assertIsNotNone(cursor.fetchone(), "transactions 테이블이 존재해야 합니다.")

            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_transactions_unique'")
            self.assertIsNotNone(cursor.fetchone(), "복합 UNIQUE 인덱스가 존재해야 합니다.")

    def test_upsert_idempotency(self):
        """동일 데이터를 여러 번 적재해도 카운트가 증가하지 않는 멱등성 검증"""
        sample_data = pd.DataFrame([
            {
                "dealDate": "2026-01-15",
                "dealYear": "2026",
                "dealMonth": "01",
                "dealDay": "15",
                "sggCd": "41115",
                "regionName": "수원시 팔달구",
                "umdNm": "매교동",
                "jibun": "123",
                "aptNm": "매교역푸르지오SKVIEW",
                "floor": 10,
                "excluUseAr": 84.95,
                "areaType": "84타입",
                "dealAmount": 95000,
                "buildYear": "2022",
                "dealType": "중개거래",
                "cdealType": None,
                "cdealDay": None,
            },
            {
                "dealDate": "2026-01-20",
                "dealYear": "2026",
                "dealMonth": "01",
                "dealDay": "20",
                "sggCd": "41117",
                "regionName": "수원시 영통구",
                "umdNm": "이의동",
                "jibun": "456",
                "aptNm": "광교자연앤힐스테이트",
                "floor": 15,
                "excluUseAr": 84.88,
                "areaType": "84타입",
                "dealAmount": 135000,
                "buildYear": "2012",
                "dealType": "중개거래",
                "cdealType": None,
                "cdealDay": None,
            },
        ])

        # 1차 적재
        total_1, inserted_1 = self.db.upsert_transactions(sample_data)
        self.assertEqual(total_1, 2)
        self.assertEqual(inserted_1, 2)

        # 2차 동일 데이터 재적재 (카운트 불변 검증)
        total_2, inserted_2 = self.db.upsert_transactions(sample_data)
        self.assertEqual(total_2, 2, "동일 데이터 재적재 시 총 건수는 2건으로 유지되어야 합니다.")
        self.assertEqual(self.db.get_count(), 2)

    def test_cancellation_update(self):
        """기존 거래가 취소(cdealType='O')로 변경되었을 때 정상 덮어쓰기 갱신 검증"""
        initial_data = pd.DataFrame([
            {
                "dealDate": "2026-02-10",
                "dealYear": "2026",
                "dealMonth": "02",
                "dealDay": "10",
                "sggCd": "41115",
                "regionName": "수원시 팔달구",
                "umdNm": "인계동",
                "jibun": "789",
                "aptNm": "수원센트럴아이파크자이",
                "floor": 8,
                "excluUseAr": 84.99,
                "areaType": "84타입",
                "dealAmount": 88000,
                "buildYear": "2023",
                "dealType": "중개거래",
                "cdealType": None,
                "cdealDay": None,
            }
        ])
        self.db.upsert_transactions(initial_data)

        # 취소 정보가 추가된 갱신 데이터
        canceled_data = pd.DataFrame([
            {
                "dealDate": "2026-02-10",
                "dealYear": "2026",
                "dealMonth": "02",
                "dealDay": "10",
                "sggCd": "41115",
                "regionName": "수원시 팔달구",
                "umdNm": "인계동",
                "jibun": "789",
                "aptNm": "수원센트럴아이파크자이",
                "floor": 8,
                "excluUseAr": 84.99,
                "areaType": "84타입",
                "dealAmount": 88000,
                "buildYear": "2023",
                "dealType": "중개거래",
                "cdealType": "O",
                "cdealDay": "2026-02-25",
            }
        ])
        self.db.upsert_transactions(canceled_data)

        df = self.db.get_all_transactions()
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["cdealType"], "O")
        self.assertEqual(df.iloc[0]["cdealDay"], "2026-02-25")

    def test_region_queries(self):
        """지역별 존재 여부 및 건수 조회 검증"""
        sample_data = pd.DataFrame([
            {
                "dealDate": "2026-03-01",
                "dealYear": "2026",
                "dealMonth": "03",
                "dealDay": "01",
                "sggCd": "41115",
                "regionName": "수원시 팔달구",
                "umdNm": "교동",
                "jibun": "1",
                "aptNm": "팔달단지",
                "floor": 5,
                "excluUseAr": 84.5,
                "dealAmount": 70000,
            }
        ])
        self.db.upsert_transactions(sample_data)

        self.assertTrue(self.db.has_region_data("41115"))
        self.assertFalse(self.db.has_region_data("99999"))
        self.assertEqual(self.db.get_region_count("41115"), 1)
        self.assertEqual(self.db.get_region_count("99999"), 0)


if __name__ == "__main__":
    unittest.main()
