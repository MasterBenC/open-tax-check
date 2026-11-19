#!/usr/bin/env python3
"""Portable Agent Skill entry point for the installed OpenTaxCheck package."""

import sys

INSTALL_HINT = (
    '  python -m pip install -e ".[excel,report]"\n'
    "  python -m playwright install chromium\n"
)

MODULE_HINTS = {
    "open_tax_check": "尚未安装 OpenTaxCheck 引擎包 open_tax_check",
    "openpyxl": "Excel 工作簿读取组件 openpyxl 未安装（可选依赖 excel）",
    "playwright": "PDF 报告渲染组件 playwright 未安装（可选依赖 report）",
}

BROWSER_HINTS = ("Executable doesn't exist", "playwright install")


def _root_module(error):
    name = getattr(error, "name", None) or ""
    return name.split(".")[0]


def _dependency_message(error):
    root = _root_module(error)
    hint = MODULE_HINTS.get(root)
    if hint is None:
        hint = f"缺少 Python 模块 {root or getattr(error, 'name', 'unknown')}"
    return f"{hint}。\n修复步骤：\n{INSTALL_HINT}"


def _fail(message):
    sys.stderr.write(f"{message}\n")
    return 2


def main():
    try:
        from open_tax_check.cli import main as cli_main
    except ModuleNotFoundError as error:
        return _fail(f"无法启动 opentaxcheck：{_dependency_message(error)}")
    try:
        return cli_main()
    except ModuleNotFoundError as error:
        return _fail(f"运行中断：{_dependency_message(error)}")
    except Exception as error:  # noqa: BLE001 - 只在缺少浏览器内核时改写提示
        message = str(error)
        if any(token in message for token in BROWSER_HINTS):
            return _fail(
                "PDF 报告渲染需要 Chromium 浏览器内核，但当前环境未安装。\n"
                "修复步骤：\n  python -m playwright install chromium"
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
