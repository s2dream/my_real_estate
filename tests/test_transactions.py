import unittest
import pandas as pd

from src.transactions import normalize_transactions
from scripts.import_csv import parse_and_transform


class TestTransactionNormalization(unittest.TestCase):
    def test_null_markers_are_canonical(self):
        frame = pd.DataFrame([{
            "dealYear": "2026", "dealMonth": "1", "dealDay": "2",
            "dealAmount": "90,000", "floor": "10", "excluUseAr": "84.9",
            "dealType": "-", "cdealType": "None", "cdealDay": "nan",
        }])
        frame["dealAmount"] = frame["dealAmount"].str.replace(",", "", regex=False)
        row = normalize_transactions(frame).iloc[0]
        self.assertEqual(row.dealType, "중개거래")
        self.assertIsNone(row.cdealType)
        self.assertIsNone(row.cdealDay)
        self.assertEqual(row.dealMonth, "01")
        self.assertEqual(row.dealDay, "02")

    def test_rich_identity_ignores_corrected_price(self):
        base = {"dealYear": "2026", "dealMonth": "01", "dealDay": "02", "sggCd": "41115",
                "aptNm": "단지", "umdNm": "동", "jibun": "1", "floor": 10,
                "excluUseAr": 84.9, "aptDong": "101", "rgstDate": "20260301"}
        rows = normalize_transactions(pd.DataFrame([{**base, "dealAmount": 90000}, {**base, "dealAmount": 91000}]))
        self.assertEqual(rows.iloc[0].transactionKey, rows.iloc[1].transactionKey)

    def test_legacy_identity_keeps_distinct_prices(self):
        base = {"dealYear": "2026", "dealMonth": "01", "dealDay": "02", "sggCd": "41115",
                "aptNm": "단지", "umdNm": "동", "jibun": "1", "floor": 10, "excluUseAr": 84.9}
        rows = normalize_transactions(pd.DataFrame([{**base, "dealAmount": 90000}, {**base, "dealAmount": 91000}]))
        self.assertNotEqual(rows.iloc[0].transactionKey, rows.iloc[1].transactionKey)

    def test_csv_import_uses_same_nulls_and_identity_fields(self):
        raw = pd.DataFrame([{
            "시군구": "경기도 수원시 팔달구 매교동", "번지": "1", "단지명": "단지",
            "전용면적(㎡)": "84.9000", "계약년월": "202601", "계약일": "2",
            "거래금액(만원)": "90,000", "동": "101", "층": "10", "건축년도": "2022",
            "해제사유발생일": "-", "거래유형": "-", "등기일자": "20260301",
        }])
        row = parse_and_transform(raw).iloc[0]
        self.assertEqual(row.aptDong, "101")
        self.assertEqual(row.rgstDate, "20260301")
        self.assertEqual(row.dealType, "중개거래")
        self.assertIsNone(row.cdealType)
        self.assertIsNone(row.cdealDay)
        self.assertEqual(len(row.transactionKey), 64)


if __name__ == "__main__":
    unittest.main()
