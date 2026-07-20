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

Normalized rows add `source_file`, `source_sheet`, `source_row`, and `mapping_version`. A valid posting date is normalized to ISO form and adds `period` (`YYYY-MM`) and `tax_year` (`YYYY`) before any hard rule runs. Later formats should also retain workbook cell ranges, OCR page and bounding box, extraction method, and confidence where applicable.

## Date handling and row completeness

The deterministic CSV adapter accepts `YYYY-MM-DD`, `YYYY/M/D`, and `YYYY.M.D`; month and day may be one or two digits for slash and dot forms. It rejects missing dates, invalid calendar dates, mixed separators, timestamps, and ambiguous localized forms instead of guessing a period.

An amount row is blocked when `voucher_no`, `date`, or `account_name` is blank. A blank debit or credit is treated as zero only when the other side is present; a row with both amounts blank is blocked. These diagnostics retain only field names and minimal source coordinates.

## Numeric handling

Use decimal arithmetic for currency. Do not use binary floating point for tax calculations. Accepted values use ASCII digits with an optional ASCII `+` or `-`, and are ordinary integers or decimals with either no grouping or valid three-digit thousands grouping. Leading and trailing whitespace is ignored, and a blank amount is zero. Each Phase 1 amount allows at most 18 integer digits and 2 fractional digits.

The sum of the absolute values of all valid debit and credit amounts must not exceed `99,999,999,999,999,999,999.99` (20 integer digits). This aggregate is accumulated with an explicit 50-digit exact decimal context. Exceeding either the per-value or aggregate limit is blocking and prevents voucher balancing and tax rules.

Malformed grouping, non-finite values such as `NaN` or `Infinity`, scientific notation, Unicode digits, internal whitespace, currency symbols, and parenthesized negatives are blocking data-quality issues. Diagnostics identify only affected fields and source coordinates; they do not echo rejected values. Preserve source values separately if cleaning changes separators, signs, or blank values.
