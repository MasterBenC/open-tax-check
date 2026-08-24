"""Validated Chromium PDF renderer for tax-health-report/v3."""

from __future__ import annotations

from base64 import b64encode
from functools import lru_cache
from html import escape
from importlib import resources
import os
from pathlib import Path
import tempfile

from .html_report import render_report_html
from .report_view import ReportViewV3

try:
    from playwright.sync_api import (
        Error as PlaywrightError,
        TimeoutError as PlaywrightTimeoutError,
        sync_playwright,
    )
except ImportError:  # pragma: no cover - exercised by explicit monkeypatch
    PlaywrightError = RuntimeError  # type: ignore[assignment,misc]
    PlaywrightTimeoutError = TimeoutError  # type: ignore[assignment,misc]
    sync_playwright = None


_MAX_REPORT_PAGES = 500
_REQUIRED_OUTLINE_TITLES = (
    "阅读提示",
    "税务健康检查结论",
    "风险全景",
    "本次税务检查地图",
    "优先处理清单",
    "重点风险详解",
    "分税种检查结果",
    "资料范围与检查边界",
    "附录",
)


class PdfExportUnavailable(RuntimeError):
    """Raised with a stable code when an official PDF cannot be published."""


def _outline_titles(items: object) -> tuple[str, ...]:
    titles: list[str] = []
    if not isinstance(items, list):
        return ()
    for item in items:
        if isinstance(item, list):
            titles.extend(_outline_titles(item))
            continue
        title = getattr(item, "title", None)
        if isinstance(title, str):
            titles.append(title)
    return tuple(titles)


def _outline_page_numbers(path: Path) -> dict[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(path, strict=True)
    numbers: dict[str, int] = {}
    pending = list(reader.outline)
    while pending:
        item = pending.pop(0)
        if isinstance(item, list):
            pending[0:0] = item
            continue
        title = getattr(item, "title", None)
        if isinstance(title, str):
            numbers[title] = reader.get_destination_page_number(item) + 1
    return numbers


def validate_report_pdf(path: Path, expected_report_id: str) -> None:
    """Reject a PDF that lacks the bounded customer-report structure."""

    try:
        from pypdf import PdfReader

        reader = PdfReader(Path(path), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted_pdf")
        pages = reader.pages
        if not 1 <= len(pages) <= _MAX_REPORT_PAGES:
            raise ValueError("invalid_page_count")
        text = "\n".join(page.extract_text() or "" for page in pages)
        if "税务健康检查报告" not in text or expected_report_id not in text:
            raise ValueError("missing_customer_identity")
        outline_titles = _outline_titles(reader.outline)
        if not all(title in outline_titles for title in _REQUIRED_OUTLINE_TITLES):
            raise ValueError("missing_report_outline")
    except PdfExportUnavailable:
        raise
    except Exception as error:
        raise PdfExportUnavailable("pdf_validation_failed") from error


@lru_cache(maxsize=1)
def _header_footer_font_style() -> str:
    font = resources.files("open_tax_check.report_assets").joinpath(
        "fonts/NotoSansSC-VF.ttf"
    ).read_bytes()
    encoded = b64encode(font).decode("ascii")
    return (
        '<style>@font-face{font-family:"OpenTaxCheck Sans";'
        f'src:url("data:font/ttf;base64,{encoded}") format("truetype");'
        'font-style:normal;font-weight:100 900}'
        '*{font-family:"OpenTaxCheck Sans"}</style>'
    )


def _header_template(view: ReportViewV3) -> str:
    entity = escape(view.executive_summary.entity_name or "未命名企业", quote=True)
    period = escape(view.meta.period_zh, quote=True)
    return (
        _header_footer_font_style()
        +
        '<div style="width:100%;font-size:8px;color:#64727d;padding:0 16mm;'
        'font-family:"OpenTaxCheck Sans";display:flex;justify-content:space-between">'
        f'<span>{entity}</span><span>{period}</span></div>'
    )


def _footer_template(view: ReportViewV3) -> str:
    report_id = escape(view.meta.report_id, quote=True)
    return (
        _header_footer_font_style()
        +
        '<div style="width:100%;font-size:8px;color:#64727d;padding:0 16mm;'
        'font-family:"OpenTaxCheck Sans";display:flex;justify-content:space-between">'
        f'<span>保密文件 · {report_id}</span>'
        '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span>'
        '</div>'
    )


def _chromium_launch_options() -> dict[str, object]:
    configured = os.environ.get("OPENTAXCHECK_CHROMIUM_EXECUTABLE")
    if configured is None:
        return {"headless": True}
    executable = Path(configured)
    if not executable.is_file():
        raise PdfExportUnavailable("pdf_renderer_unavailable")
    return {"headless": True, "executable_path": str(executable.resolve())}


def write_report_pdf(view: ReportViewV3, path: Path) -> Path:
    """Render, validate, and atomically publish the official customer PDF."""

    if sync_playwright is None:
        raise PdfExportUnavailable("pdf_renderer_unavailable")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    browser = None
    external_requests: list[str] = []
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=destination.parent, prefix=f".{destination.name}.",
            suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        html = render_report_html(view)
        try:
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(**_chromium_launch_options())
                page = browser.new_page(
                    locale="zh-CN",
                    viewport={"width": 1240, "height": 1754},
                )

                def enforce_offline(route: object) -> None:
                    request = route.request  # type: ignore[attr-defined]
                    if request.url.startswith(("about:", "data:", "blob:")):
                        route.continue_()  # type: ignore[attr-defined]
                        return
                    external_requests.append(request.url)
                    route.abort()  # type: ignore[attr-defined]

                page.route("**/*", enforce_offline)
                page.set_content(html, wait_until="load", timeout=120_000)
                if external_requests:
                    raise PdfExportUnavailable("pdf_external_resource_blocked")
                try:
                    page.wait_for_function(
                        "document.fonts.status === 'loaded'",
                        timeout=120_000,
                    )
                    if not page.evaluate(
                        'document.fonts.check(\'12px "OpenTaxCheck Sans"\')'
                    ):
                        raise PdfExportUnavailable("pdf_font_unavailable")
                except PlaywrightTimeoutError as error:
                    raise PdfExportUnavailable("pdf_font_unavailable") from error
                page.emulate_media(media="print")
                pdf_options = {
                    "path": str(temporary_path),
                    "format": "A4",
                    "print_background": True,
                    "prefer_css_page_size": True,
                    "display_header_footer": True,
                    "header_template": _header_template(view),
                    "footer_template": _footer_template(view),
                    "margin": {"top": "18mm", "right": "0", "bottom": "17mm", "left": "0"},
                    "tagged": True,
                    "outline": True,
                }
                page.pdf(**pdf_options)
                page_numbers = _outline_page_numbers(temporary_path)
                page.evaluate(
                    """pageNumbers => {
                        for (const item of document.querySelectorAll(
                            '.toc-page[data-title]'
                        )) {
                            const pageNumber = pageNumbers[item.dataset.title];
                            if (!Number.isInteger(pageNumber)) {
                                throw new Error('missing_toc_destination');
                            }
                            item.textContent = String(pageNumber);
                        }
                    }""",
                    page_numbers,
                )
                page.pdf(**pdf_options)
                if _outline_page_numbers(temporary_path) != page_numbers:
                    raise PdfExportUnavailable("pdf_validation_failed")
                browser.close()
                browser = None
        except PdfExportUnavailable:
            raise
        except PlaywrightTimeoutError as error:
            raise PdfExportUnavailable("pdf_render_timeout") from error
        except PlaywrightError as error:
            message = str(error).casefold()
            code = (
                "pdf_renderer_unavailable"
                if "executable doesn't exist" in message or "browser executable" in message
                else "pdf_render_failed"
            )
            raise PdfExportUnavailable(code) from error
        validate_report_pdf(temporary_path, view.meta.report_id)
        os.replace(temporary_path, destination)
        temporary_path = None
        return destination
    except PdfExportUnavailable:
        raise
    except Exception as error:
        raise PdfExportUnavailable("pdf_render_failed") from error
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


__all__ = ["PdfExportUnavailable", "validate_report_pdf", "write_report_pdf"]
