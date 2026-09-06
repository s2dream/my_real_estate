import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from analysis.reassessment_csv import load_csv_snapshot, NAMES


def trade(**changes):
    row = {
        "NO": "1", "시군구": "경기도 수원시 팔달구 매교동", "번지": "300",
        "단지명": NAMES[0], "전용면적(㎡)": "84.9700", "계약년월": "202503",
        "계약일": "3", "거래금액(만원)": "95,000", "동": "101", "층": "10",
        "건축년도": "2023", "해제사유발생일": "-", "거래유형": "중개거래", "등기일자": "25.06.01",
    }
    row.update(changes)
    return row


class TestReassessmentCSV(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, filename, rows, year=2025):
        path = self.directory / filename
        with path.open("w", encoding="cp949", newline="") as handle:
            handle.write(f'"계약일자 : {year}-01-01 ~ {year}-12-31"\n')
            pd.DataFrame(rows).to_csv(handle, index=False)
        return path

    def load(self, start="2024-01-01", end="2026-09-06"):
        return load_csv_snapshot(self.directory, pd.Timestamp(start), pd.Timestamp(end))

    def test_rereported_active_row_survives_cancellation_in_either_order(self):
        active = trade()
        canceled = trade(**{"NO": "2", "동": "-", "등기일자": "-", "해제사유발생일": "20250310"})
        path = self.write("snapshot.csv", [active, canceled])
        before = path.read_bytes()
        selected, audit, conflicts = self.load()
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected.iloc[0].building, "101")
        self.assertEqual(selected.iloc[0].registrationDate, "25.06.01")
        self.assertEqual(selected.iloc[0].source_row, 1)
        self.assertEqual(audit["source_key_conflicts"], 1)
        self.assertEqual(audit["cancelled_rows"], 1)
        self.assertEqual(len(conflicts), 2)
        self.assertEqual(set(conflicts.conflict_type), {"active_and_cancelled_same_key"})
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(selected.cdealType.isna().all())
        self.assertTrue(selected.cdealDay.isna().all())
        self.write("snapshot.csv", [canceled, active])
        selected_reversed, _, _ = self.load()
        self.assertEqual(selected_reversed.iloc[0].dealAmount, 95000)
        self.assertEqual(selected_reversed.iloc[0].building, "101")

    def test_identical_file_copy_and_padded_dates_do_not_double_count(self):
        original = self.write("a.csv", [trade(), trade(**{"NO": "2", "계약일": "03"})])
        (self.directory / "b.CSV").write_bytes(original.read_bytes())
        active, audit, conflicts = self.load()
        self.assertEqual(len(active), 1)
        self.assertEqual(audit["identical_files_skipped"], 1)
        self.assertEqual(audit["active_duplicate_rows_removed"], 1)
        self.assertEqual(len(conflicts), 2)
        self.assertEqual(active.iloc[0].duplicate_count, 2)
        self.assertEqual(active.iloc[0].id, self.load()[0].iloc[0].id)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(active.dealDate))
        json.dumps(audit, ensure_ascii=False)

    def test_overlapping_different_snapshots_are_rejected(self):
        self.write("old.csv", [trade()])
        self.write("new.csv", [trade(**{"해제사유발생일": "20250310"})])
        with self.assertRaisesRegex(ValueError, "중첩"):
            self.load()

    def test_disjoint_years_and_filters_and_invalid_values(self):
        self.write("2025.csv", [
            trade(), trade(**{"NO": "2", "계약일": "31", "계약년월": "202502"}),
            trade(**{"NO": "3", "거래금액(만원)": "not a price"}),
            trade(**{"NO": "4", "거래금액(만원)": "95000.5"}),
            trade(**{"NO": "5", "단지명": "다른 단지"}),
            trade(**{"NO": "6", "전용면적(㎡)": "85"}),
            trade(**{"NO": "7", "계약일": "4", "층": "2.5"}),
            trade(**{"NO": "8", "계약년월": "202x503"}),
            trade(**{"NO": "9", "계약일": "5", "층": "inf"}),
        ])
        self.write("2026.csv", [trade(**{"계약년월": "202609", "계약일": "7"})], year=2026)
        active, audit, _ = self.load()
        self.assertEqual(len(active), 3)
        self.assertEqual(audit["raw_target_rows"], 8)
        self.assertEqual(audit["invalid_date_rows"], 2)
        self.assertEqual(audit["invalid_price_rows"], 2)
        self.assertEqual(audit["invalid_floor_rows"], 2)
        self.assertEqual(audit["outside_date_rows"], 1)
        self.assertEqual(active.floor.isna().sum(), 2)

    def test_different_units_with_same_public_key_require_review(self):
        self.write("snapshot.csv", [trade(), trade(**{"NO": "2", "동": "102"})])
        with self.assertRaisesRegex(ValueError, "서로 다른 동"):
            self.load()

    def test_missing_cancellation_column_is_not_assumed_active(self):
        row = trade()
        row.pop("해제사유발생일")
        self.write("snapshot.csv", [row])
        with self.assertRaisesRegex(ValueError, "필수 열"):
            self.load()

    def test_no_target_rows_produces_empty_frame_with_schema(self):
        self.write("snapshot.csv", [trade(**{"단지명": "다른 단지"})])
        active, audit, conflicts = self.load()
        self.assertTrue(active.empty)
        self.assertTrue(conflicts.empty)
        self.assertIn("source_file", active)
        self.assertEqual(audit["clean_rows"], 0)


if __name__ == "__main__":
    unittest.main()
