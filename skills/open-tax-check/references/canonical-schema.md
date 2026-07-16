# Canonical journal schema

## Required fields

| Canonical field | Meaning | Examples of accepted source headers |
| --- | --- | --- |
| `voucher_no` | Voucher identifier | 凭证号, 凭证编号, 记账凭证号 |
| `date` | Posting or voucher date | 日期, 凭证日期, 记账日期 |
| `account_name` | Account or detailed account name | 科目名称, 会计科目, 科目 |
| `debit` | Debit amount | 借方发生额, 借方金额, 借方 |
| `credit` | Credit amount | 贷方发生额, 贷方金额, 贷方 |

## Optional fields

| Canonical field | Meaning | Examples |
| --- | --- | --- |
| `account_code` | Account code | 科目代码, 科目编码 |
| `summary` | Voucher narrative | 摘要, 业务说明, 凭证摘要 |
| `counterparty` | Counterparty | 对方单位, 往来单位, 交易对手 |

## Mapping control

Every source header must either map to a canonical field or be explicitly excluded by a reviewer. Missing required fields and unresolved headers block automatic calculation. Multiple source headers that resolve to the same canonical field are recorded in `conflicts` and block automatic calculation until a reviewer selects the intended source. Mapping output must include a mapping version and be retained with the case.

## Source lineage

Normalized rows add `source_file`, `source_sheet`, `source_row`, and `mapping_version`. Later formats should also retain workbook cell ranges, OCR page and bounding box, extraction method, and confidence where applicable.

## Numeric handling

Use decimal arithmetic for currency. Do not use binary floating point for tax calculations. Preserve source values separately if cleaning changes separators, signs, or blank values.
