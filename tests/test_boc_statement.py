import unittest

import pymupdf

from app import example_pdf, parse_pdf


def make_statement(debit_summary="40.00"):
    with pymupdf.open() as doc:
        page = doc.new_page()

        def write(x, y, text):
            page.insert_text((x, y), text, fontname="china-s" if not text.isascii() else "helv", fontsize=9)

        write(180, 85, "中国银行信用卡账单(2026年01月)")
        write(215, 270, "账单日")
        write(205, 320, "2026-01-03")
        write(18, 375, "人民币账户交易明细")
        write(160, 420, "本期支出金额")
        write(275, 420, "本期存入金额")
        write(169, 475, debit_summary)
        write(280, 475, "108.00")

        def row(top, entries):
            for index in range(6):
                left = 17.6 + index * 93.4
                page.draw_rect((left, top, left + 93.4, top + 32), fill=(1, 1, 1))
            for x, offset, text in entries:
                write(x, top + offset, text)

        row(525, [(53, 19, "12/31"), (147, 19, "01/01"), (242, 19, "0000"),
                  (305, 12, "Mock"), (305, 25, "Cafe"), (520, 19, "40.00")])
        row(557, [(53, 19, "01/01"), (147, 19, "01/02"), (242, 19, "0000"),
                  (305, 19, "BOCNET"), (427, 19, "100.00")])
        row(589, [(53, 19, "01/02"), (147, 19, "01/03"), (242, 19, "0000"),
                  (305, 19, "Mock Cafe"), (427, 19, "8.00")])
        row(621, [(53, 19, "01/03"), (147, 19, "01/03"), (242, 19, "0000"),
                  (303, 19, "已为您减免本年度年费")])
        return doc.tobytes()


class BankOfChinaStatementTest(unittest.TestCase):
    def test_columns_dates_and_reconciliation(self):
        rows, warnings, expected, page_count = parse_pdf(make_statement())
        self.assertEqual(warnings, [])
        self.assertEqual((expected, page_count), (3200, 1))
        self.assertEqual([(r["day"], r["merchant"], r["kind"], r["amount"], r["page"]) for r in rows], [
            ("2025-12-31", "Mock Cafe", "消费", 4000, 1),
            ("2026-01-01", "BOCNET", "还款", 10000, 1),
            ("2026-01-02", "Mock Cafe", "退款", 800, 1),
        ])

    def test_mismatch_does_not_report_reconciled(self):
        rows, warnings, expected, _ = parse_pdf(make_statement("41.00"))
        self.assertEqual(len(rows), 3)
        self.assertIsNone(expected)
        self.assertTrue(any("合计" in warning for warning in warnings))

    def test_generic_sample_still_works(self):
        rows, warnings, expected, _ = parse_pdf(example_pdf().body)
        self.assertEqual((len(rows), warnings, expected), (3, [], 16200))
