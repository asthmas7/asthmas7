import unittest

from fastapi.testclient import TestClient

from app import app
from spending_review import review_candidates


def purchase(id_, month, amount=5000, merchant="示例商户", tag=""):
    return dict(id=id_, day=f"2026-{month}-10", amount=amount, merchant=merchant,
                card="示例卡", kind="消费", category="休闲娱乐", tag=tag)


class SpendingReviewTest(unittest.TestCase):
    def test_past_feedback_and_recurrence_prioritize_a_specific_purchase(self):
        rows = [purchase(1, "08", tag="可减少"), purchase(2, "09"), purchase(3, "10")]
        candidates = review_candidates(rows, "2026-10")
        self.assertEqual([candidate["交易编号"] for candidate in candidates], [3])
        self.assertEqual({reason["类型"] for reason in candidates[0]["依据"]}, {"历史反馈", "疑似周期扣费"})
        rows[1]["tag"] = "合理支出"
        self.assertEqual(review_candidates(rows, "2026-10"), [])

    def test_duplicate_needs_review_even_when_past_spending_was_accepted(self):
        rows = [purchase(1, "09", tag="合理支出"), purchase(2, "10"), purchase(3, "10")]
        rows.append({**purchase(4, "10"), "kind": "退款", "original_id": 2})
        candidates = review_candidates(rows, "2026-10")
        self.assertEqual([candidate["交易编号"] for candidate in candidates], [3])
        self.assertEqual(candidates[0]["依据"][0]["类型"], "疑似重复")

    def test_devtools_discovery_responds_without_404(self):
        with TestClient(app) as client:
            response = client.get("/.well-known/appspecific/com.chrome.devtools.json")
            self.assertEqual((response.status_code, response.json()), (200, {}))
