"""Parser for the Bank of China bilingual RMB credit card statement."""
import re
from datetime import date
from decimal import Decimal

TITLE = re.compile(r"中国银行信用卡账单\s*[（(]\s*(\d{4})年\s*(\d{1,2})月")
SHORT_DATE = re.compile(r"\d{2}/\d{2}")
AMOUNT = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}")
REPAYMENT = re.compile(r"还款|還款|REPAYMENT|(?:^|\W)BOCNET(?:$|\W)", re.I)
NON_RMB = re.compile(r"(?:美元|港币|港元|欧元|日元|外币)账户交易明细")


def amount_cents(value):
    return int(Decimal(value.replace(",", "")) * 100)


def value_below(words, label, pattern):
    header = next((word for word in words if word[4] == label), None)
    if header is None:
        return None
    x = (header[0] + header[2]) / 2
    matches = [word for word in words if header[1] < word[1] < header[1] + 85
               and abs((word[0] + word[2]) / 2 - x) < 32 and pattern.fullmatch(word[4])]
    return min(matches, key=lambda word: word[1])[4] if matches else None


def transaction_date(value, closing):
    month, day = map(int, value.split("/"))
    year = closing.year - ((month, day) > (closing.month, closing.day))
    return date(year, month, day).isoformat()


def table_rows(page, words):
    # The repeated fills identify the row boundaries even when a description wraps.
    width = page.rect.width
    bands = sorted({(path["rect"].y0, path["rect"].y1) for path in page.get_drawings()
                    if path["type"] in {"f", "fs"} and abs(path["rect"].x0 / width - .0296) < .008
                    and abs(path["rect"].x1 / width - .1865) < .008})
    for top, bottom in bands:
        cells = [[] for _ in range(6)]
        for word in words:
            x = (word[0] + word[2]) / (2 * width)
            y = (word[1] + word[3]) / 2
            col = int((x - .0296) / .1568) if x >= .0296 else -1
            if top <= y < bottom and 0 <= col < len(cells):
                cells[col].append(word)
        values = [" ".join(word[4] for word in sorted(cell, key=lambda word: (round(word[1]), word[0])))
                  for cell in cells]
        if any(SHORT_DATE.fullmatch(values[index]) for index in (0, 1)):
            yield values


def parse_boc(document):
    first = document[0]
    text = first.get_text()
    title = TITLE.search(text)
    if not title or "人民币账户交易明细" not in text:
        return None
    words = first.get_text("words")
    closing_text = value_below(words, "账单日", re.compile(r"\d{4}-\d{2}-\d{2}"))
    if not closing_text:
        return [], ["识别到中国银行信用卡账单，但未找到完整的账单日。"], None
    try:
        closing = date.fromisoformat(closing_text)
        if (closing.year, closing.month) != tuple(map(int, title.groups())):
            raise ValueError()
    except ValueError:
        return [], ["中国银行账单日无效，或与账单标题月份不符。"], None

    rows, warnings = [], []
    deposits = expenditures = excluded = 0
    for page in document:
        page_words = page.get_text("words")
        foreign = NON_RMB.search(page.get_text())
        limit = page.rect.height
        if foreign:
            limit = min((word[1] for word in page_words if NON_RMB.search(word[4])), default=0)
        for day, posting, card_suffix, merchant, credit, debit in table_rows(
            page, [word for word in page_words if word[1] < limit]
        ):
            if not credit and not debit and re.fullmatch(r"已为您减免本年度年费[。.]?", merchant):
                continue
            try:
                if not (SHORT_DATE.fullmatch(day) and SHORT_DATE.fullmatch(posting)
                        and re.fullmatch(r"\d{4}", card_suffix) and merchant
                        and bool(credit) != bool(debit) and AMOUNT.fullmatch(credit or debit)):
                    raise ValueError()
                iso_day = transaction_date(day, closing)
                transaction_date(posting, closing)
                amount = amount_cents(credit or debit)
                if amount > 10**11:
                    raise ValueError()
            except ValueError:
                warnings.append(f"第 {page.number + 1} 页有一笔中国银行交易无法完整识别，请核对原页。")
                continue
            if credit:
                deposits += amount
                kind = "还款" if REPAYMENT.search(merchant) else "退款"
                if kind == "还款":
                    excluded += amount
            else:
                expenditures += amount
                kind = "消费"
            rows.append(dict(day=iso_day, merchant=merchant, amount=amount, kind=kind,
                             page=page.number + 1, source=f"{day} | {posting} | {merchant} | {credit} | {debit}"))
        if foreign:
            warnings.append(f"第 {page.number + 1} 页起的外币账户明细未导入。")
            break

    expected = None
    debit_total = value_below(words, "本期支出金额", AMOUNT)
    credit_total = value_below(words, "本期存入金额", AMOUNT)
    if debit_total and credit_total:
        if (expenditures, deposits) == (amount_cents(debit_total), amount_cents(credit_total)):
            expected = expenditures - deposits + excluded
        else:
            warnings.append("识别出的存入或支出合计与人民币账单不符，请核对是否有漏行。")
    return rows, warnings, expected
