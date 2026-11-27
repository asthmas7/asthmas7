import hashlib
import json
import os
import random
import re
import sqlite3
from uuid import uuid4
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pymupdf as fitz
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from openai import OpenAIError
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from boc_statement import parse_boc
from openai_client import OpenAIConfigurationError, OpenAIResponseError, configured
from statement_ai import analysis_snapshot, analyze, extract_pdf
from spending_review import review_candidates

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BILL_DATA_DIR", ROOT / "data"))
CATEGORIES = ["餐饮美食", "交通出行", "生活购物", "居住缴费", "休闲娱乐", "医疗健康", "其他"]
KINDS = {"消费": "消费", "退款": "退款", "转账": "转账", "还款": "还款",
         "PURCHASE": "消费", "REFUND": "退款", "TRANSFER": "转账", "REPAYMENT": "还款"}
KEYWORDS = {
    "餐饮美食": ("咖啡", "餐厅", "外卖", "美团", "coffee", "restaurant"),
    "交通出行": ("地铁", "滴滴", "加油", "铁路", "metro"),
    "生活购物": ("淘宝", "京东", "超市", "market"),
    "居住缴费": ("房租", "电费", "水费", "物业", "rent"),
    "休闲娱乐": ("电影", "会员", "音乐", "影院"),
    "医疗健康": ("医院", "药房", "体检"),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY, name TEXT, card TEXT, card_type TEXT,
    digest TEXT UNIQUE, pdf BLOB, created TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY, batch INTEGER REFERENCES batches(id),
    day TEXT NOT NULL, merchant TEXT NOT NULL, amount INTEGER NOT NULL,
    kind TEXT NOT NULL, category TEXT NOT NULL, card TEXT NOT NULL,
    page INTEGER, source TEXT, tag TEXT DEFAULT '', note TEXT DEFAULT '',
    sample INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS refund_links (
    refund INTEGER PRIMARY KEY REFERENCES transactions(id),
    purchase INTEGER UNIQUE REFERENCES transactions(id)
);
CREATE TABLE IF NOT EXISTS rules (merchant TEXT PRIMARY KEY, category TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_previews (
    token TEXT PRIMARY KEY, digest TEXT NOT NULL, card TEXT NOT NULL, rows TEXT NOT NULL,
    created TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ai_analyses (snapshot_hash TEXT PRIMARY KEY, result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY);
"""
app = FastAPI(title="月账 · 个人账单分析")


@contextmanager
def database():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA / "ledger.sqlite3")
    db.row_factory = sqlite3.Row
    try:
        db.executescript(SCHEMA)
        with db:
            yield db
    finally:
        db.close()


def merchant_key(value):
    return " ".join(value.casefold().split())


def classify(db, merchant):
    key = merchant_key(merchant)
    rule = db.execute("SELECT category FROM rules WHERE merchant=?", (key,)).fetchone()
    if rule:
        return rule["category"]
    return next((category for category, words in KEYWORDS.items()
                 if any(word in key for word in words)), "其他")


def seed(db):
    if not db.execute("INSERT OR IGNORE INTO settings VALUES ('sample_initialized')").rowcount:
        return
    randomizer = random.Random(23)
    merchants = [
        ("山茶咖啡", "街角餐厅", "美团外卖", "社区食堂"),
        ("城市地铁", "滴滴出行", "铁路购票"),
        ("盒马超市", "京东商城", "无印良品"),
        ("公寓房租", "物业缴费", "城市电费"),
        ("星河影院", "视频会员", "音乐会员"),
        ("社区药房", "健康体检"),
        ("快递服务", "文印服务"),
    ]
    for year, last_month in [(2025, 12), (2026, 11)]:
        for month in range(1, last_month + 1):
            rows = [
                (4, "山茶咖啡", 3800, "消费", "餐饮美食", "招商 · 信用卡", "", ""),
                (5, "城市地铁", 600, "消费", "交通出行", "工商 · 借记卡", "", ""),
                (6, "视频会员", 2500, "消费", "休闲娱乐", "招商 · 信用卡", "", ""),
                (7, "盒马超市", 18650, "消费", "生活购物", "招商 · 信用卡", "", ""),
                (8, "京东商城", 12900, "退款", "生活购物", "招商 · 信用卡", "", ""),
                (9, "信用卡还款", 260000, "还款", "其他", "工商 · 借记卡", "", ""),
            ]
            for index, category in enumerate(CATEGORIES):
                count = [13, 8, 8, 3, 4, 2, 2][index]
                for n in range(count):
                    amount = randomizer.randint(1200, [24000, 12000, 39000, 28000, 15000, 8000, 4000][index])
                    if index == 3 and n == 0:
                        amount = 280000
                    merchant = randomizer.choice(merchants[index])
                    tag = "可减少" if month == 11 and n == 0 and index in (2, 4) else ""
                    rows.append((randomizer.randrange(1, 29), merchant, amount, "消费", category,
                                 "招商 · 信用卡" if index in (0, 2, 4) else "工商 · 借记卡", tag, ""))
            if month == 11:
                rows.append((6, "视频会员", 2500, "消费", "休闲娱乐", "招商 · 信用卡", "疑似浪费", "核对是否重复订阅"))
            db.executemany(
                "INSERT INTO transactions(day,merchant,amount,kind,category,card,tag,note,sample) "
                "VALUES (?,?,?,?,?,?,?,?,1)",
                [(f"{year}-{month:02d}-{day:02d}", *details) for day, *details in rows],
            )


@app.get("/api/state")
def state():
    with database() as db:
        seed(db)
        return {
            "transactions": [dict(r) for r in db.execute(
                "SELECT transactions.*,refund_links.purchase AS original_id "
                "FROM transactions LEFT JOIN refund_links ON refund_links.refund=transactions.id "
                "ORDER BY day DESC,id DESC")],
            "batches": [dict(r) for r in db.execute(
                "SELECT id,name,card,card_type,created FROM batches ORDER BY id DESC")],
            "rules": [dict(r) for r in db.execute("SELECT * FROM rules ORDER BY merchant")],
            "categories": CATEGORIES,
            "ai_enabled": configured(),
        }


@app.delete("/api/sample")
def clear_sample():
    with database() as db:
        db.execute("INSERT OR IGNORE INTO settings VALUES ('sample_initialized')")
        db.execute("DELETE FROM transactions WHERE sample=1")
    return {"ok": True}


LINE = re.compile(
    r"^(?P<day>\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\s+(?P<merchant>.+?)\s+"
    r"(?P<amount>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})\s+"
    r"(?P<kind>消费|退款|转账|还款|PURCHASE|REFUND|TRANSFER|REPAYMENT)$", re.I
)
TOTAL = re.compile(r"^(?:消费净额|NET SPENDING)\s+([+-]?[\d,]+\.\d{2})$", re.I)


def parse_pdf(raw):
    rows, warnings = [], []
    expected = None
    with fitz.open(stream=raw, filetype="pdf") as document:
        if document.needs_pass:
            raise ValueError("PDF 已加密，请先在本地解密。")
        page_count = len(document)
        if page_count > 100:
            raise ValueError("每份 PDF 最多支持 100 页。")
        if page_count:
            boc = parse_boc(document)
            if boc is not None:
                rows, warnings, expected = boc
                if not rows:
                    warnings.insert(0, "没有识别到中国银行账单交易，请核对账单明细。")
                return rows, warnings, expected, page_count
        for page in document:
            for source in page.get_text().splitlines():
                source = " ".join(source.split())
                total = TOTAL.fullmatch(source)
                if total:
                    expected = int(Decimal(total[1].replace(",", "")) * 100)
                    continue
                match = LINE.fullmatch(source)
                if not match:
                    if re.match(r"^\d{4}[-/.]", source):
                        warnings.append(f"第 {page.number + 1} 页未识别：{source[:140]}")
                    continue
                try:
                    day = date(*map(int, re.split(r"[-/.]", match["day"]))).isoformat()
                    amount = abs(int(Decimal(match["amount"].replace(",", "")) * 100))
                    if amount > 10**11:
                        raise ValueError()
                except (ValueError, OverflowError):
                    warnings.append(f"第 {page.number + 1} 页的日期或金额无效：{source[:140]}")
                    continue
                rows.append(dict(day=day, merchant=match["merchant"], amount=amount,
                                 kind=KINDS[match["kind"].upper()], page=page.number + 1, source=source))
    if not rows:
        warnings.insert(0, "没有识别到交易。请核对 PDF 版式，扫描件和未知版式需要额外适配。")
    return rows, warnings, expected, page_count


def prepare_rows(db, rows, card):
    existing = {(r["day"], merchant_key(r["merchant"]), r["amount"], r["kind"])
                for r in db.execute(
                    "SELECT day,merchant,amount,kind FROM transactions WHERE card=? AND sample=0", (card,))}
    purchases = [dict(row) for row in db.execute(
        "SELECT day,merchant,amount,category FROM transactions "
        "WHERE card=? AND sample=0 AND kind='消费' AND id NOT IN (SELECT purchase FROM refund_links)", (card,))]
    for row in rows:
        key = (row["day"], merchant_key(row["merchant"]), row["amount"], row["kind"])
        row["duplicate"] = key in existing
        existing.add(key)
        row["category"] = classify(db, row["merchant"])
    purchases.extend(row for row in rows if row["kind"] == "消费" and not row["duplicate"])
    for row in rows:
        if row["kind"] == "退款":
            matches = refund_candidates(row, purchases)
            if len(matches) == 1:
                row["category"] = matches[0]["category"]


def refund_candidates(refund, purchases):
    return [purchase for purchase in purchases if purchase["day"] <= refund["day"]
            and purchase["amount"] == refund["amount"]
            and merchant_key(purchase["merchant"]) == merchant_key(refund["merchant"])]


def link_refunds(db, batch_id, card):
    purchases = list(db.execute(
        "SELECT id,day,merchant,amount FROM transactions "
        "WHERE card=? AND sample=0 AND kind='消费' AND id NOT IN (SELECT purchase FROM refund_links)", (card,)))
    refunds = db.execute(
        "SELECT id,day,merchant,amount FROM transactions WHERE batch=? AND kind='退款' ORDER BY day,id",
        (batch_id,))
    for refund in refunds:
        matches = refund_candidates(refund, purchases)
        if len(matches) == 1:
            original = matches[0]
            db.execute("INSERT INTO refund_links VALUES (?,?)", (refund["id"], original["id"]))
            purchases.remove(original)


async def read_pdf(file):
    raw = await file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024 or not raw.startswith(b"%PDF-"):
        raise HTTPException(400, "请选择 10 MB 以内的 PDF 文件。")
    return raw


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


AI_ERRORS = (OpenAIError, OpenAIConfigurationError, OpenAIResponseError, ValueError)


def preview_pdf(raw, card):
    file_hash = digest(raw)
    with database() as db:
        if db.execute("SELECT 1 FROM batches WHERE digest=?", (file_hash,)).fetchone():
            raise HTTPException(409, "这份 PDF 已导入。")
    try:
        rows, warnings, expected, page_count = parse_pdf(raw)
    except (ValueError, fitz.FileDataError) as error:
        raise HTTPException(400, "无法解析 PDF。 " + str(error)) from error

    token = None
    if not rows and configured() and page_count:
        try:
            rows, ai_warnings = extract_pdf(raw, page_count)
        except AI_ERRORS:
            warnings.append("AI 解析暂不可用，请核对 API 配置、用量或稍后重试。也可人工补录。")
        else:
            if rows:
                warnings = ["本地未识别到交易，以下为 AI 从原 PDF 提取的草稿。请逐笔核对金额、类型和页码。",
                            *ai_warnings]
                token = uuid4().hex
                with database() as db:
                    db.execute("DELETE FROM ai_previews WHERE created < datetime('now', '-1 day')")
                    db.execute("INSERT INTO ai_previews(token,digest,card,rows) VALUES(?,?,?,?)",
                               (token, file_hash, card, json.dumps(rows, ensure_ascii=False)))
            else:
                warnings += ["AI 也未识别到可用交易。", *ai_warnings]

    with database() as db:
        prepare_rows(db, rows, card)
    return {"rows": rows, "warnings": warnings, "expected": expected, "page_count": page_count,
            "preview_token": token}


@app.post("/api/preview")
async def preview_upload(file: UploadFile = File(), card: str = Form(min_length=1, max_length=80)):
    if not card.strip():
        raise HTTPException(400, "请输入卡片别名。")
    return await run_in_threadpool(preview_pdf, await read_pdf(file), card.strip())


class Row(BaseModel):
    day: date
    merchant: str = Field(min_length=1, max_length=200)
    amount: int = Field(ge=0, le=10**11)
    kind: Literal["消费", "退款", "转账", "还款"]
    category: str
    index: int | None = Field(default=None, ge=0)
    page: int | None = Field(default=None, ge=1)


class Import(BaseModel):
    card: str = Field(min_length=1, max_length=80)
    card_type: Literal["借记卡", "信用卡"]
    rows: list[Row] = Field(min_length=1, max_length=10000)
    preview_token: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


@app.post("/api/import")
async def import_upload(background: BackgroundTasks, file: UploadFile = File(), payload: str = Form()):
    raw = await read_pdf(file)
    try:
        batch = Import.model_validate_json(payload)
        if not batch.card.strip() or any(not row.merchant.strip() or row.category not in CATEGORIES
                                         for row in batch.rows):
            raise ValueError()
        originals, _, _, page_count = parse_pdf(raw)
        if batch.preview_token:
            with database() as db:
                cached = db.execute(
                    "SELECT rows FROM ai_previews WHERE token=? AND digest=? AND card=? "
                    "AND created >= datetime('now', '-1 day')",
                    (batch.preview_token, digest(raw), batch.card.strip())).fetchone()
            if cached is None:
                raise HTTPException(400, "AI 预览已失效，或文件与卡片已更换。请重新解析。")
            originals = json.loads(cached["rows"])
        indices = [row.index for row in batch.rows if row.index is not None]
        if len(set(indices)) != len(indices) or any(index >= len(originals) for index in indices):
            raise ValueError()
        if any(row.index is None and (row.page is None or row.page > page_count) for row in batch.rows):
            raise ValueError()
    except (ValueError, fitz.FileDataError) as error:
        raise HTTPException(400, "请检查卡片别名、交易字段和 PDF。") from error
    try:
        with database() as db:
            cursor = db.execute("INSERT INTO batches(name,card,card_type,digest,pdf) VALUES (?,?,?,?,?)",
                                (file.filename, batch.card.strip(), batch.card_type, digest(raw), raw))
            batch_id = cursor.lastrowid
            db.executemany(
                "INSERT INTO transactions(batch,day,merchant,amount,kind,category,card,page,source) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [(batch_id, row.day.isoformat(), row.merchant.strip(), row.amount, row.kind,
                  row.category, batch.card.strip(),
                  row.page if row.index is None else originals[row.index]["page"],
                  "人工补录" if row.index is None else originals[row.index]["source"]) for row in batch.rows],
            )
            link_refunds(db, batch_id, batch.card.strip())
            db.execute("DELETE FROM ai_previews WHERE digest=?", (digest(raw),))
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "这份 PDF 已导入。") from error
    months = sorted({row.day.isoformat()[:7] for row in batch.rows}, reverse=True)
    if configured():
        background.add_task(analyze_import, months)
    return {"count": len(batch.rows), "months": months}


class AnalysisRequest(BaseModel):
    month: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    comparison: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")


def load_analysis(query):
    with database() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT t.id,t.day,t.card,t.merchant,t.amount,t.kind,t.category,t.tag,r.purchase AS original_id "
            "FROM transactions t LEFT JOIN refund_links r ON r.refund=t.id WHERE t.sample=0 ORDER BY t.day,t.id")]
        if not any(row["day"][:7] == query.month for row in rows):
            raise HTTPException(404, "所选月份没有已入账的真实账单。")
        snapshot = analysis_snapshot(rows, query.month, query.comparison)
        fingerprint = digest(json.dumps(snapshot, sort_keys=True).encode())
        cached = db.execute("SELECT result FROM ai_analyses WHERE snapshot_hash=?", (fingerprint,)).fetchone()
    return snapshot, fingerprint, json.loads(cached["result"]) if cached else None


@app.get("/api/analysis")
def saved_analysis(month: str, comparison: str, sample: bool = False):
    try:
        query = AnalysisRequest(month=month, comparison=comparison)
    except ValueError as error:
        raise HTTPException(422, "请选择有效的月份。") from error
    if sample:
        with database() as db:
            seed(db)
            rows = [dict(row) for row in db.execute(
                "SELECT id,day,card,merchant,amount,kind,category,tag FROM transactions WHERE sample=1")]
        return {"analysis": None, "candidates": review_candidates(rows, query.month)}
    snapshot, _, result = load_analysis(query)
    return {"analysis": result, "candidates": snapshot["待复盘交易"]}


@app.post("/api/analysis")
def generate_analysis(query: AnalysisRequest):
    snapshot, fingerprint, cached = load_analysis(query)
    if cached is not None:
        return {"analysis": cached, "candidates": snapshot["待复盘交易"]}
    if not configured():
        raise HTTPException(503, "请配置 OPENAI_API_KEY 后开启 AI 分析。")
    try:
        result = analyze(snapshot).model_dump()
    except AI_ERRORS as error:
        raise HTTPException(502, "AI 分析暂不可用，请检查 API 配置或稍后重试。") from error
    valid_ids = {candidate["交易编号"] for candidate in snapshot["待复盘交易"]}
    suggestions = {item["transaction_id"]: item for item in reversed(result["transaction_review"])
                   if item["transaction_id"] in valid_ids}
    result["transaction_review"] = list(suggestions.values())
    with database() as db:
        db.execute("INSERT OR IGNORE INTO ai_analyses VALUES(?,?)",
                   (fingerprint, json.dumps(result, ensure_ascii=False)))
    return {"analysis": result, "candidates": snapshot["待复盘交易"]}


def analyze_import(months):
    for period in months:
        year, month = map(int, period.split("-"))
        if year == 1 and month == 1:
            continue
        previous = f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"
        try:
            generate_analysis(AnalysisRequest(month=period, comparison=previous))
        except HTTPException:
            # The import is already committed. Users can retry analysis from the dashboard.
            continue


@app.get("/api/batches/{batch_id}/pdf")
def batch_pdf(batch_id: int):
    with database() as db:
        row = db.execute("SELECT pdf FROM batches WHERE id=?", (batch_id,)).fetchone()
    if not row:
        raise HTTPException(404, "找不到账单。")
    return Response(row["pdf"], media_type="application/pdf",
                    headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


class Feedback(BaseModel):
    category: str
    tag: Literal["", "可减少", "疑似浪费", "合理支出"]
    note: str = Field(max_length=2000)
    learn: bool = False


@app.patch("/api/transactions/{transaction_id}")
def feedback(transaction_id: int, change: Feedback):
    if change.category not in CATEGORIES:
        raise HTTPException(400, "未知类别。")
    with database() as db:
        row = db.execute("SELECT merchant FROM transactions WHERE id=?", (transaction_id,)).fetchone()
        if not row:
            raise HTTPException(404, "找不到交易。")
        db.execute("UPDATE transactions SET category=?,tag=?,note=? WHERE id=?",
                   (change.category, change.tag, change.note, transaction_id))
        if change.learn:
            db.execute("INSERT INTO rules VALUES(?,?) ON CONFLICT(merchant) DO UPDATE SET category=excluded.category",
                       (merchant_key(row["merchant"]), change.category))
    return {"ok": True}


@app.delete("/api/rules")
def delete_rule(merchant: str):
    with database() as db:
        db.execute("DELETE FROM rules WHERE merchant=?", (merchant,))
    return {"ok": True}


@app.get("/api/example.pdf")
def example_pdf(card_type: Literal["借记卡", "信用卡"] = "信用卡"):
    lines = ["BANK STATEMENT / SAMPLE", "CNY | DATE   MERCHANT   AMOUNT   TYPE"]
    if card_type == "借记卡":
        lines += ["2026-11-08 City Metro 6.00 PURCHASE", "2026-11-10 Card Payment 1800.00 REPAYMENT",
                  "2026-11-12 Family Transfer 200.00 TRANSFER", "NET SPENDING 6.00"]
    else:
        lines += ["2026-11-05 Pine Coffee 32.00 PURCHASE", "2026-11-08 Fresh Market 158.80 PURCHASE",
                  "2026-11-11 Fresh Market 28.80 REFUND", "NET SPENDING 162.00"]
    with fitz.open() as document:
        page = document.new_page()
        for index, line in enumerate(lines):
            page.insert_text((55, 65 + index * 28), line, fontsize=12)
        return Response(document.tobytes(), media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="sample-statement.pdf"'})


@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
def chrome_devtools_discovery():
    return {}


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="web")
