import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from fastapi.testclient import TestClient

import app
from statement_ai import Analysis, Extraction


class StatementAiTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        data = patch.object(app, "DATA", Path(self.directory.name))
        data.start()
        self.addCleanup(data.stop)
        self.client = TestClient(app.app)
        self.addCleanup(self.client.close)
        with pymupdf.open() as document:
            document.new_page().insert_text((40, 60), "Unsupported statement layout")
            self.pdf = document.tobytes()

    def preview(self, raw=None):
        return self.client.post("/api/preview", data={"card": "私人卡片别名"},
                                files={"file": ("sample.pdf", raw or self.pdf, "application/pdf")})

    def confirm(self, preview):
        rows = [{**row, "index": index} for index, row in enumerate(preview["rows"])]
        payload = {"card": "私人卡片别名", "card_type": "信用卡",
                   "rows": rows, "preview_token": preview["preview_token"]}
        return self.client.post("/api/import", data={"payload": json.dumps(payload)},
                                files={"file": ("sample.pdf", self.pdf, "application/pdf")})

    def test_fallback_requires_confirmation_and_auto_analysis_uses_aggregates(self):
        extraction = Extraction.model_validate({"rows": [
            {"day": "2026-10-15", "merchant": "私人商户摘要", "amount_cents": 1234,
             "kind": "待核对", "page": 1, "excerpt": "transaction excerpt"},
            {"day": "2026-10-16", "merchant": "无效页", "amount_cents": 10,
             "kind": "消费", "page": 2, "excerpt": ""},
        ], "warnings": []})
        report = Analysis(summary="本月概况", categories=[], trends=[], anomalies=[], review=[])
        with (patch.object(app, "configured", return_value=True),
              patch("statement_ai.generate_structured", side_effect=[extraction, report]) as api):
            preview = self.preview().json()
            self.assertEqual(len(preview["rows"]), 1)
            self.assertTrue(preview["preview_token"])
            self.assertTrue(any("无效" in warning for warning in preview["warnings"]))
            self.assertEqual(self.confirm(preview).status_code, 400)
            self.assertEqual(api.call_count, 1)
            preview["rows"][0]["kind"] = "消费"
            outcome = self.confirm(preview)
            self.assertEqual(outcome.json(), {"count": 1, "months": ["2026-10"]})
            self.assertEqual(api.call_count, 2)
            data = json.loads(api.call_args.args[0][0]["text"])
            self.assertEqual(data["目标月汇总"]["消费净额_分"], 1234)
            self.assertIsNone(data["对比月汇总"])
            self.assertNotIn("私人", json.dumps(data, ensure_ascii=False))
            params = {"month": "2026-10", "comparison": "2026-09"}
            self.assertEqual(self.client.get("/api/analysis", params=params).json()["analysis"], report.model_dump())
            self.assertEqual(self.client.post("/api/analysis", json=params).json()["analysis"], report.model_dump())
            self.assertEqual(api.call_count, 2)
            row = next(row for row in self.client.get("/api/state").json()["transactions"] if not row["sample"])
            self.assertEqual((row["page"], row["source"]), (1, "AI 摘录：transaction excerpt"))

    def test_local_success_and_missing_key_do_not_call_fallback(self):
        with patch.object(app, "extract_pdf") as extract:
            with patch.object(app, "configured", return_value=True):
                self.assertEqual(len(self.preview(app.example_pdf().body).json()["rows"]), 3)
            with patch.object(app, "configured", return_value=False):
                self.assertEqual(self.preview().json()["rows"], [])
            extract.assert_not_called()

    def test_api_error_keeps_manual_fallback_available(self):
        with (patch.object(app, "configured", return_value=True),
              patch.object(app, "extract_pdf", side_effect=app.OpenAIResponseError("test"))):
            data = self.preview().json()
            self.assertEqual(data["rows"], [])
            self.assertIsNone(data["preview_token"])
            self.assertTrue(any("人工补录" in warning for warning in data["warnings"]))
