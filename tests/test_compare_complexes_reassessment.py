"""Synthetic checks for the reassessment's data and statistical safeguards.

These fixtures deliberately do not use the repository's transaction database.
"""

import json
import unittest

import numpy as np
import pandas as pd

from analysis import compare_complexes_reassessment as reassessment


START = pd.Timestamp("2025-01-01")
AS_OF = pd.Timestamp("2025-12-31")


def report_metadata(as_of=AS_OF, lag_days=0):
    return {
        "start_date": str(START.date()), "as_of": str(as_of.date()),
        "lag_days": lag_days, "database_sha256": "synthetic",
        "query_rows_sha256": "synthetic", "python": "test",
    }


def transaction(row_id, name=reassessment.SK, date="2025-02-10", **changes):
    row = {
        "id": row_id, "aptNm": name, "dealDate": date,
        "dealYear": date[:4], "dealMonth": date[5:7], "dealDay": date[8:],
        "sggCd": "41115", "umdNm": "테스트동", "jibun": "123",
        "floor": 8, "excluUseAr": 84.97, "areaType": "84타입",
        "dealAmount": 90000, "dealType": "중개거래", "cdealType": None,
        "cdealDay": None, "updated_at": "2025-12-01 00:00:00",
    }
    row.update(changes)
    return row


def prepare(rows):
    return reassessment.prepare_data(pd.DataFrame(rows), START, AS_OF)


def market_fixture(months=8, sk_effect=0.8):
    """Unequal cell sizes, market movement, floor effects, and residual noise."""
    rows = []
    for month in range(1, months + 1):
        for tier, floor_base in enumerate((2, 8, 17)):
            for name in reassessment.NAMES:
                is_sk = name == reassessment.SK
                count = 2 + ((month if is_sk else 2 * month) + tier) % 3
                for j in range(count):
                    price = (
                        8 + month * 0.08 + tier * 0.14
                        + is_sk * (sk_effect + 0.04 * (month % 3 - 1))
                        + 0.035 * (j - (count - 1) / 2)
                    )
                    day = 1 + tier * 8 + j + 4 * is_sk
                    rows.append(transaction(
                        len(rows) + 1, name, f"2025-{month:02d}-{day:02d}",
                        floor=floor_base + j % 3,
                        dealAmount=round(price * 10000),
                    ))
    return prepare(rows)


class TestTransactionReconciliation(unittest.TestCase):
    def test_padding_duplicates_merge_without_losing_known_trade_type(self):
        rows = [
            transaction(1, dealMonth="2", dealDay="10", dealType="직거래",
                        updated_at="2025-03-01"),
            transaction(2, dealMonth="02", dealDay="10", dealType=None,
                        updated_at="2025-04-01"),
            transaction(3, reassessment.IP),
        ]
        legacy, clean, duplicates, audit = prepare(rows)
        self.assertEqual(len(legacy), 3)
        self.assertEqual(len(clean), 2)
        self.assertEqual(len(duplicates), 2)
        self.assertEqual(audit["duplicate_rows_removed"], 1)
        self.assertEqual(clean.loc[clean.aptNm == reassessment.SK, "id"].item(), 2)
        self.assertEqual(clean.loc[clean.aptNm == reassessment.SK, "dealType"].item(), "직거래")

    def test_cancellation_on_older_snapshot_cannot_be_revived(self):
        rows = [
            transaction(1, cdealType="O", updated_at="2025-03-01"),
            transaction(2, updated_at="2025-04-01"),
            transaction(3, reassessment.IP),
        ]
        _, clean, duplicates, audit = prepare(rows)
        self.assertEqual(clean.aptNm.tolist(), [reassessment.IP])
        self.assertEqual(audit["cancelled_keys_removed"], 1)
        self.assertTrue(duplicates.group_cancelled.all())

    def test_cancellation_date_alone_excludes_trade(self):
        _, clean, _, audit = prepare([
            transaction(1, cdealDay="20250301"),
            transaction(2, reassessment.IP, cdealType=" - ", cdealDay="None"),
        ])
        self.assertEqual(clean.id.tolist(), [2])
        self.assertEqual(audit["cancelled_keys_removed"], 1)

    def test_invalid_prices_are_removed_but_unknown_floors_remain_descriptive(self):
        rows = [transaction(1, dealAmount="90,000", floor="8")]
        for row_id, value in enumerate((None, "bad", 0, -1, np.inf), start=2):
            rows.append(transaction(row_id, dealAmount=value))
        for row_id, value in enumerate((None, "bad", 0, -1, 2.5, np.inf), start=7):
            rows.append(transaction(row_id, floor=value, dealAmount=90000 + row_id))
        rows.append(transaction(13, excluUseAr="bad"))
        rows.append(transaction(14, date="invalid"))
        rows.append(transaction(15, reassessment.IP, floor=9))
        _, clean, _, audit = prepare(rows)
        self.assertEqual(audit["invalid_price_rows"], 5)
        self.assertEqual(audit["invalid_floor_rows"], 6)
        self.assertEqual(audit["outside_or_invalid_area_rows"], 1)
        self.assertEqual(audit["invalid_date_rows"], 1)
        self.assertEqual(len(clean), 8)
        self.assertEqual(int(clean.floor.isna().sum()), 6)
        self.assertEqual(clean.loc[clean.id == 1, "price"].item(), 9.0)
        self.assertTrue(np.isfinite(clean.price).all())
        comparable = reassessment.comparable_sample(clean, AS_OF)
        self.assertTrue(comparable.floor.notna().all())


class TestComparableObservations(unittest.TestCase):
    def test_full_calendar_retains_missing_prices_and_zero_counts(self):
        _, clean, _, _ = prepare([
            transaction(1, date="2025-01-10"),
            transaction(2, reassessment.IP, date="2025-03-10"),
        ])
        monthly = reassessment.month_table(clean, START, pd.Timestamp("2025-03-31")).set_index("month")
        self.assertEqual(monthly.index.tolist(), ["2025-01", "2025-02", "2025-03"])
        self.assertEqual(monthly.loc["2025-02", ["sk_n", "ip_n"]].tolist(), [0, 0])
        self.assertTrue(monthly.gap_median.isna().all())
        self.assertTrue(monthly.loc["2025-02", ["sk_median", "ip_median"]].isna().all())
        self.assertEqual(monthly.loc["2025-01", "sk_median"], 9.0)

    def test_common_months_floor_range_and_matched_cells_exclude_unsupported_trades(self):
        _, clean, _, _ = prepare([
            transaction(1, date="2025-01-01", floor=1),
            transaction(2, date="2025-02-01", floor=3),
            transaction(3, date="2025-02-02", floor=8),
            transaction(4, reassessment.IP, date="2025-02-03", floor=9),
            transaction(5, reassessment.IP, date="2025-02-04", floor=18),
            transaction(6, date="2025-03-01", floor=18),
            transaction(7, reassessment.IP, date="2025-03-02", floor=3),
        ])
        common = reassessment.comparable_sample(clean, AS_OF)
        self.assertNotIn(1, set(common.id))
        self.assertEqual(set(common.month), {"2025-02", "2025-03"})
        self.assertTrue(common.floor.between(3, 18).all())
        matched = reassessment.matched_cells(common)
        self.assertEqual(set(matched.id), {3, 4})
        self.assertEqual(set(reassessment.cell_table(matched).cell), {"2025-02 / 6–15층"})

    def test_disjoint_floor_ranges_have_no_comparable_sample(self):
        _, clean, _, _ = prepare([
            transaction(1, floor=2), transaction(2, floor=3),
            transaction(3, reassessment.IP, floor=20),
        ])
        self.assertTrue(reassessment.comparable_sample(clean, AS_OF).empty)

    def test_descriptive_tables_share_supported_months_floors_and_cutoff(self):
        _, clean, _, _ = prepare([
            transaction(1, date="2025-01-01", floor=1, dealAmount=70000),
            transaction(2, date="2025-02-01", floor=3, dealAmount=80000),
            transaction(3, date="2025-02-02", floor=8, dealAmount=90000),
            transaction(4, reassessment.IP, date="2025-02-03", floor=9, dealAmount=80000),
            transaction(5, reassessment.IP, date="2025-02-04", floor=18, dealAmount=87000),
            transaction(6, date="2025-03-01", floor=18, dealAmount=100000),
            transaction(7, reassessment.IP, date="2025-03-02", floor=3, dealAmount=85000),
            transaction(8, date="2025-02-05", floor=20, dealAmount=200000),
            transaction(9, date="2025-07-01", floor=8, dealAmount=110000),
            transaction(10, reassessment.IP, date="2025-07-02", floor=9, dealAmount=90000),
            transaction(11, date="2025-12-01", floor=8, dealAmount=300000),
            transaction(12, reassessment.IP, date="2025-12-02", floor=9, dealAmount=250000),
        ])
        result = reassessment.analyze(clean, START, AS_OF, lag_days=153)
        self.assertEqual(result["cutoff"], pd.Timestamp("2025-07-31"))
        self.assertEqual(set(result["common"].id), {2, 3, 4, 5, 6, 7, 9, 10})
        distributions = result["distributions"]
        all_rows = distributions[distributions.scope == "전체 유효 거래"].set_index("aptNm")
        common_rows = distributions[distributions.scope == "공통월·공통 층 범위"].set_index("aptNm")
        self.assertEqual(all_rows.loc[reassessment.SK, "n"], 7)
        self.assertEqual(common_rows.loc[reassessment.SK, "n"], 4)
        self.assertEqual(common_rows.loc[reassessment.IP, "n"], 4)
        self.assertAlmostEqual(common_rows.loc[reassessment.SK, "mean"], 9.5)
        self.assertAlmostEqual(common_rows.loc[reassessment.SK, "q25"], 8.75)
        self.assertAlmostEqual(common_rows.loc[reassessment.IP, "median"], 8.6)

        quarters = result["quarters"].set_index("quarter")
        self.assertEqual(quarters.index.tolist(), ["2025Q1", "2025Q3"])
        self.assertEqual(quarters.loc["2025Q1", "months"], "2025-02, 2025-03")
        self.assertEqual(quarters.loc["2025Q3", "months"], "2025-07")
        self.assertEqual(quarters.loc["2025Q1", ["sk_n", "ip_n"]].tolist(), [3, 3])
        self.assertAlmostEqual(quarters.loc["2025Q1", "sk_median"], 9.0)
        floors = result["floors"].set_index("floor_tier")
        self.assertEqual(int(floors.sk_n.sum()), 4)
        self.assertEqual(int(floors.ip_n.sum()), 4)
        self.assertEqual(floors.loc["16층 이상", "sk_n"], 1)
        self.assertAlmostEqual(floors.loc["16층 이상", "sk_share_pct"], 25.0)
        self.assertEqual(result["matched_months"].month.tolist(), ["2025-02", "2025-07"])


class TestStatisticalInterpretation(unittest.TestCase):
    def test_invalid_raw_observation_is_removed_before_exportable_analysis(self):
        for bad_value in ("bad", np.inf):
            with self.subTest(dealAmount=bad_value):
                raw, _, _, _ = market_fixture()
                raw["dealAmount"] = raw["dealAmount"].astype(object)
                raw.loc[raw.index[0], "dealAmount"] = bad_value
                _, clean, _, audit = reassessment.prepare_data(raw, START, AS_OF)
                self.assertEqual(audit["invalid_price_rows"], 1)
                self.assertEqual(len(clean), len(raw) - 1)
                self.assertTrue(np.isfinite(clean.price).all())
                result = reassessment.analyze(clean, START, AS_OF)
                self.assertGreater(result["primary"]["estimate"], 0)
                self.assertTrue(all(np.isfinite(model["estimate"])
                                    for model in result["models"] if "estimate" in model))
                # Results must remain exportable as standards-compliant JSON.
                json.dumps(result["models"], allow_nan=False)

    def test_weighted_cell_difference_matches_fixed_effect_estimate(self):
        _, clean, _, _ = market_fixture()
        matched = reassessment.matched_cells(reassessment.comparable_sample(clean, AS_OF))
        cells = reassessment.cell_table(matched)
        estimate = reassessment.fit_gap(matched, "fixture", "price ~ is_sk + C(cell)")
        expected = np.average(cells.gap, weights=cells.weight)
        self.assertEqual(estimate["status"], "ok")
        self.assertAlmostEqual(estimate["estimate"], expected, places=10)
        self.assertLess(estimate["ci_low"], expected)
        self.assertGreater(estimate["ci_high"], expected)

    def test_reversed_prices_reverse_report_interpretation(self):
        _, clean, _, audit = market_fixture(sk_effect=-0.8)
        result = reassessment.analyze(clean, START, AS_OF, lag_days=0)
        primary = result["primary"]
        self.assertLess(primary["ci_high"], 0)
        report = reassessment.render_report(clean, audit, result, report_metadata(), [])
        conclusion = report.split("## 1.")[0]
        self.assertIn("센트럴아이파크자이가 더 높은 가격", conclusion)
        self.assertNotIn("SKVIEW가 더 높은 가격", conclusion)
        self.assertIn(f"{primary['estimate']:+.3f}억원", conclusion)

    def test_report_and_model_labels_describe_data_without_historical_comparison(self):
        _, clean, _, audit = market_fixture()
        result = reassessment.analyze(clean, START, AS_OF, lag_days=0)
        report = reassessment.render_report(clean, audit, result, report_metadata(), [])
        labels = "\n".join(model["label"] for model in result["models"])
        for obsolete_word in ("기존", "이전", "재분석", "DB 정리", "legacy"):
            with self.subTest(word=obsolete_word):
                self.assertNotIn(obsolete_word, report)
                self.assertNotIn(obsolete_word, labels)
        self.assertEqual(result["primary"]["n"], len(result["matched"]))
        self.assertIn("포함된 관측월", report)
        self.assertIn("중앙 50%", report)
        self.assertIn("세부 구간의 정밀도", report)

    def test_empty_common_support_produces_explicit_unavailable_report(self):
        fixtures = {
            "disjoint floors": [transaction(1, floor=2),
                                transaction(2, reassessment.IP, floor=20)],
            "disjoint months": [transaction(1, date="2025-02-01"),
                                transaction(2, reassessment.IP, date="2025-03-01")],
            "one complex": [transaction(1)],
        }
        for scenario, rows in fixtures.items():
            with self.subTest(scenario=scenario):
                _, clean, _, audit = prepare(rows)
                result = reassessment.analyze(clean, START, AS_OF, lag_days=0)
                self.assertTrue(result["common"].empty)
                self.assertTrue(result["quarters"].empty)
                self.assertTrue(result["matched_months"].empty)
                self.assertNotIn("estimate", result["primary"])
                json.dumps(result["models"], allow_nan=False)
                report = reassessment.render_report(clean, audit, result, report_metadata(), [])
                conclusion = report.split("## 1.")[0]
                self.assertIn("비교 가능한 표본이 부족", conclusion)
                self.assertIn("공통 관측월 없음", report)
                self.assertNotIn("더 높은 가격에 거래됐다는 근거", conclusion)

    def test_rank_deficiency_does_not_produce_estimate_or_interval(self):
        _, clean, _, _ = market_fixture()
        clean["floor"] = clean["is_sk"] + 8
        model = reassessment.fit_gap(clean, "collinear", "price ~ is_sk + floor")
        self.assertIn("산출 불가", model["status"])
        self.assertNotIn("estimate", model)
        self.assertNotIn("ci_low", model)

    def test_few_month_clusters_return_point_estimate_without_precision(self):
        _, clean, _, _ = market_fixture(months=3)
        model = reassessment.fit_gap(clean, "few clusters", "price ~ is_sk + C(cell)")
        self.assertIn("점추정만", model["status"])
        self.assertGreater(model["estimate"], 0)
        self.assertNotIn("ci_low", model)
        self.assertNotIn("p_value", model)

    def test_log_point_estimate_keeps_percentage_units_with_few_clusters(self):
        _, clean, _, _ = market_fixture(months=3)
        # Fixed ratios give an independently known percentage interpretation.
        clean["price"] = np.where(clean.is_sk == 1, 11.0, 10.0)
        model = reassessment.fit_gap(clean, "log few clusters", "np.log(price) ~ is_sk + C(cell)")
        self.assertEqual(model["unit"], "%")
        self.assertAlmostEqual(model["estimate"], 10.0, places=8)
        self.assertNotIn("ci_low", model)

    def test_one_complex_does_not_produce_a_comparison(self):
        _, clean, _, _ = market_fixture(months=3)
        model = reassessment.fit_gap(clean[clean.is_sk == 1], "one complex", "price ~ is_sk")
        self.assertIn("양쪽 거래 필요", model["status"])
        self.assertNotIn("estimate", model)


if __name__ == "__main__":
    unittest.main()
