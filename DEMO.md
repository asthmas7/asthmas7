# 月账 · 本地 Demo

导入借记卡和信用卡 PDF，核对消费、查看多月和年度支出，记录每月复盘反馈。

## 启动

在仓库目录运行以下命令。需要 Python 3.10 或更新版本。

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 。Windows 可用 `.venv\Scripts\python.exe` 替换上述 Python 路径。

## 试用

1. 首次打开已生成从 2025 年 1 月至 2026 年 11 月的演示交易。选择月份、卡片和类别，点击趋势柱或类别查看明细。
2. 在「导入与核对」下载两份样例 PDF，一次选中两份。为每份填写卡片别名、选择卡类型，解析后核对净额，逐份确认入账。
3. 点开一笔交易，修改类别、复盘标记和备注，勾选「以后同名商户使用这个分类」。在「复盘与规则」可以查看和撤销规则。
4. 在「趋势与对比」选择任意两个月，滚动查看跨年趋势；年度柱可跳转到该年最近有账单的月份。
5. 试用后可在月度总览移除演示交易。已导入账单和分类规则会保留。

## 导入边界和本地数据

- 支持中国银行双语人民币信用卡账单（表头含交易日、银行记账日、交易描述、存入、支出）。按交易日统计，跨年时以账单日补全日期。解析后用账单的存入与支出合计核对，并从消费净额排除还款。存入栏非还款项默认按退款处理，可在确认前调整。
- 也支持可提取文本、每笔交易在同一行的样例版式。字段顺序为日期、商户、金额、类型，使用空白分隔。日期支持年-月-日、年/月/日和年.月.日。金额保留两位小数。类型可用消费、退款、转账、还款，或样例里的英文类型。PDF 可选用独立的 `NET SPENDING 162.00` 或 `消费净额 162.00` 行用于对账。
- 其他银行或其他账单版式仍需单独适配。扫描件 OCR、加密 PDF 和外币换算超出此版本。只应导入人民币账单。遇到漏识别的交易，可在核对页手动补录并填写原始页码。
- 退款在发生月冲减消费。唯一匹配同卡、同商户、同金额且日期不晚于退款的原消费时，会展示关联。部分退款、歧义匹配和先录退款后录原消费的情形按独立交易处理。转账和还款留在明细，排除在消费汇总之外。
- 应用默认只监听本机。交易、原始 PDF 和反馈存入 `data/ledger.sqlite3`。停掉服务后可复制整个 `data/` 目录备份。可用 `BILL_DATA_DIR` 指定其他数据目录。

## 试用 OpenAI 调用模块

1. 登录 [OpenAI API 平台](https://platform.openai.com/api-keys)，选择要使用的项目，点击 **Create new secret key**，创建后复制密钥。API 独立于 ChatGPT 订阅计费，可在 [Billing](https://platform.openai.com/settings/organization/billing/overview) 检查余额或付款设置。
2. 重新运行依赖安装命令。用 Bash 在当前终端安全输入密钥，输入内容不会回显，也不会作为命令写入历史。

   ```bash
   read -rsp 'OpenAI API key: ' OPENAI_API_KEY
   printf '\n'
   export OPENAI_API_KEY
   .venv/bin/python -c 'from openai_client import generate_text; print(generate_text("请只回复：连接成功"))'
   ```

模块默认使用 `gpt-4.1-mini`，可用环境变量 `OPENAI_MODEL` 或调用时的 `model=` 覆盖。调用使用 Responses API，并传入 `store=False`。导入和账单页面暂未接入这个模块。密钥应通过本机环境变量或云环境的密钥设置提供。
