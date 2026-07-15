# Initial rule catalog

Rules are period-sensitive. Confirm that each authority and threshold applies to the review period before release.

## CIT-BE-001 — Business entertainment deduction

- Engine result: calculated, awaiting professional review.
- Formula: deductible amount is the lower of 60% of actual business-entertainment expense and 0.5% of qualifying revenue; tax adjustment equals actual expense minus deductible amount.
- Key controls: reconcile the expense ledger and revenue base to the annual CIT return; investigate entertainment disguised in other accounts separately.
- Authority: [Enterprise Income Tax Law Implementing Regulations, Article 43](https://jdjc.mof.gov.cn/fgzd/202201/t20220125_3784682.htm) and [State Taxation Administration guidance](https://www.chinatax.gov.cn/chinatax/n810356/n3010387/c5240872/content.html).

## IIT-SHL-001 — Individual shareholder borrowing candidate

- Engine result: candidate only.
- Trigger boundary: positive receivable from an identified individual shareholder, outstanding at year-end, with business purpose not confirmed.
- The engine may calculate a 20% scenario exposure, but this is not an automatic conclusion or final liability.
- Required evidence: shareholder identity, loan agreement, fund use, year-end repayment, subsequent repayment, and relevant corporate resolutions.
- Authority: [Cai Shui (2003) No. 158](https://fgk.chinatax.gov.cn/zcfgk/c102416/c5202842/content.html).

## SEM-EXP-001 — Expense description/account mismatch

- Engine result: unreviewed semantic candidate.
- Example pattern: gift, tobacco, alcohol, tea, banquet, KTV, or golf language posted to office, travel, or meeting expense.
- Required review: business purpose, recipient, invoice, approval, employee benefit or gift treatment, VAT treatment, and CIT classification.

## SEM-PAY-001 — Payroll/labor narrative with unusual counter-account

- Engine result: unreviewed semantic candidate.
- Example pattern: salary or labor-service narrative posted against an individual payable, advance, or other unusual account.
- Required review: payroll register, IIT withholding return, bank payment trail, contracts, social insurance, and invoice or labor evidence.

## Versioning

Changing a formula, threshold, trigger, status boundary, or authority requires a new rule version and regression test. Wording-only changes that do not alter behavior may retain the version but should be documented.
