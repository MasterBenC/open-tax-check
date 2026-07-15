"""Deterministic candidate generation for optional semantic review."""

from __future__ import annotations

import hashlib


SEMANTIC_RULES = [
    {
        "rule_id": "SEM-EXP-001",
        "rule_version": "0.1.0",
        "keywords": ["中秋", "端午", "礼品", "礼品卡", "茶叶", "烟", "酒", "高档餐饮", "ktv", "高尔夫"],
        "mismatched_accounts": ["办公费", "差旅费", "会议费"],
        "hypothesis": "Possible entertainment, gift, welfare, or personal-consumption reclassification.",
    },
    {
        "rule_id": "SEM-PAY-001",
        "rule_version": "0.1.0",
        "keywords": ["发工资", "工资代发", "发劳务", "劳务费"],
        "mismatched_accounts": ["其他应付款-个人", "预付款项"],
        "hypothesis": "Possible payroll or service-payment classification and withholding mismatch.",
    },
]


def _finding_id(rule_id: str, entry: dict) -> str:
    raw = "|".join(
        [
            rule_id,
            str(entry.get("source_file", "")),
            str(entry.get("source_sheet", "")),
            str(entry.get("source_row", "")),
            str(entry.get("voucher_no", "")),
        ]
    )
    return f"{rule_id}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"


def detect_semantic_candidates(entries: list[dict]) -> list[dict]:
    candidates = []
    for entry in entries:
        summary = str(entry.get("summary") or "").lower()
        account = str(entry.get("account_name") or "").lower()
        for rule in SEMANTIC_RULES:
            matched_keywords = [keyword for keyword in rule["keywords"] if keyword.lower() in summary]
            matched_accounts = [name for name in rule["mismatched_accounts"] if name.lower() in account]
            if not matched_keywords or not matched_accounts:
                continue
            candidates.append(
                {
                    "finding_id": _finding_id(rule["rule_id"], entry),
                    "rule_id": rule["rule_id"],
                    "rule_version": rule["rule_version"],
                    "status": "candidate",
                    "review_status": "unreviewed",
                    "severity": "medium",
                    "confidence": "rule_candidate",
                    "hypothesis": rule["hypothesis"],
                    "matched_keywords": matched_keywords,
                    "matched_accounts": matched_accounts,
                    "required_evidence": ["invoice", "contract_or_approval", "recipient_identity", "business_purpose"],
                    "source": {
                        "source_file": entry.get("source_file"),
                        "source_sheet": entry.get("source_sheet"),
                        "source_row": entry.get("source_row"),
                        "voucher_no": entry.get("voucher_no"),
                    },
                    "entry": {
                        "account_name": entry.get("account_name"),
                        "summary": entry.get("summary"),
                        "debit": entry.get("debit"),
                        "credit": entry.get("credit"),
                    },
                }
            )
    return candidates

