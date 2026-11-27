"""Find transactions worth reviewing from the user's own spending history."""
from collections import Counter, defaultdict
from statistics import median


def review_candidates(rows, month, limit=8):
    selected = [row for row in rows if row["day"][:7] == month and row["kind"] == "消费"]
    refunded = {row["original_id"] for row in rows if row.get("original_id")}
    month_index = int(month[:4]) * 12 + int(month[5:7])

    def key(row):
        return " ".join(row["merchant"].casefold().split())

    def group(row):
        return row["day"], row["card"], key(row), row["amount"]

    duplicates = Counter(group(row) for row in selected)
    history = defaultdict(list)
    for row in rows:
        previous_index = int(row["day"][:4]) * 12 + int(row["day"][5:7])
        if 1 <= month_index - previous_index <= 6 and row["kind"] == "消费" and row["id"] not in refunded:
            history[key(row)].append(row)

    candidates = []
    for row in selected:
        if row["tag"] or row["id"] in refunded:
            continue
        previous = history[key(row)]
        unnecessary = sum(item["tag"] in {"可减少", "疑似浪费"} for item in previous)
        accepted = sum(item["tag"] == "合理支出" for item in previous)
        reasons, score = [], 0
        if duplicates[group(row)] > 1:
            reasons.append({"类型": "疑似重复", "同日同卡同商户同额笔数": duplicates[group(row)]})
            score += 5
        if unnecessary > accepted:
            reasons.append({"类型": "历史反馈", "标记可减少或疑似浪费的笔数": unnecessary, "合理支出笔数": accepted})
            score += 4
        same_amount_periods = {item["day"][:7] for item in previous if item["amount"] == row["amount"]}
        if len(same_amount_periods) >= 2 and not accepted:
            reasons.append({"类型": "疑似周期扣费", "过去六个月相同金额出现月数": len(same_amount_periods)})
            score += 2
        if len(previous) >= 3:
            typical = int(median(item["amount"] for item in previous))
            if typical > 0 and row["amount"] >= typical * 2 and row["amount"] - typical >= 5000:
                reasons.append({"类型": "金额明显高于以往", "历史同商户单笔中位数_分": typical})
                score += 3
        if reasons:
            candidates.append((score, {
                "交易编号": row["id"], "类别": row["category"], "金额_分": row["amount"], "依据": reasons,
            }))
    candidates.sort(key=lambda entry: (-entry[0], -entry[1]["金额_分"], entry[1]["交易编号"]))
    return [candidate for _, candidate in candidates[:limit]]
