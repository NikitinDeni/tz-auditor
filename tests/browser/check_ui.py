"""Optional browser smoke test: run against a local preview with Playwright installed.

Example: python tests/browser/check_ui.py http://127.0.0.1:8001
Only synthetic inputs; screenshots are saved in ignored outputs/.
"""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
OUTPUT = ROOT / "outputs"
OUTPUT.mkdir(exist_ok=True)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(chromium_sandbox=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE_URL)
    expect(page.get_by_text("Готов к проверке", exact=True)).to_be_visible()
    expect(page.locator("#submit")).to_be_disabled()

    def upload(name):
        page.locator("#file").set_input_files(ROOT / "tests/fixtures/synthetic" / name)
        page.locator("#submit").click()
        expect(page.locator("#state")).to_contain_text("Анализ завершён")

    upload("shelves_conflict.docx")
    expect(page.locator("article.finding")).to_have_count(1)
    expect(page.locator(".badge")).to_have_text("Подтверждено")
    expect(page.locator(".explanation")).to_contain_text("2000 мм и 2200 мм")
    expect(page.locator(".coords").filter(has_text="Абзац: 4")).to_be_visible()
    expect(
        page.locator(".coords").filter(has_text="Таблица: 0 · строка: 1 · ячейка: 1")
    ).to_be_visible()
    expect(page.locator("details")).not_to_have_attribute("open", "")
    expect(page.locator("pre")).to_be_hidden()
    page.screenshot(path=OUTPUT / "ui-conflict-desktop.png", full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=OUTPUT / "ui-conflict-mobile.png", full_page=True)
    upload("shelves.docx")
    expect(page.locator("article.finding")).to_have_count(0)
    expect(
        page.get_by_text("В рамках реализованных проверок противоречия не обнаружены", exact=True)
    ).to_be_visible()
    page.screenshot(path=OUTPUT / "ui-no-findings-mobile.png", full_page=True)
    page.locator("summary").click()
    expect(page.locator("pre")).to_be_visible()
    assert json.loads(page.locator("pre").inner_text())["findings"] == []

    page.locator("#file").set_input_files(
        {
            "name": "large.docx",
            "mimeType": "application/octet-stream",
            "buffer": b"x" * (10 * 1024 * 1024 + 1),
        }
    )
    expect(
        page.get_by_text("Файл больше 10 МиБ. Выберите документ меньшего размера.")
    ).to_be_visible()
    expect(page.locator("#submit")).to_be_disabled()
    page.locator("#file").set_input_files(
        {"name": "bad.docx", "mimeType": "application/octet-stream", "buffer": b"invalid"}
    )
    page.locator("#submit").click()
    expect(page.locator("#placeholder[role=alert]")).to_be_visible()
    expect(page.locator("#placeholder-text")).to_contain_text("Некорректный")
    expect(page.locator("#results")).to_be_hidden()

    # Hold a request to verify loading state, then return synthetic hostile text.
    pending = []
    page.route("**/audit", lambda route: pending.append(route))
    page.locator("#file").set_input_files(ROOT / "tests/fixtures/synthetic/shelves.docx")
    page.locator("#submit").click()
    expect(page.locator(".loading")).to_be_visible()
    expect(page.locator("#submit")).to_be_disabled()
    expect(page.locator("#file")).to_be_disabled()
    hostile = '<img src=x onerror="window.injected=true">'
    response = {
        "filename": hostile,
        "document_id": hostile,
        "warnings": [hostile],
        "limitations": [hostile],
        "findings": [
            {
                "category": "numeric_contradiction",
                "status": "needs_review",
                "explanation": hostile,
                "recommendation": hostile,
                "evidence": [
                    {
                        "block_id": hostile,
                        "text": hostile,
                        "start": 0,
                        "end": 2,
                        "locator": {"paragraph_index": 0},
                    }
                ],
            }
        ],
    }
    assert pending
    pending.pop().fulfill(status=200, content_type="application/json", body=json.dumps(response))
    expect(page.locator(".badge")).to_have_text("Требует проверки")
    expect(page.locator(".filename")).to_have_text(hostile)
    expect(page.locator("blockquote")).to_have_text(hostile)
    assert page.locator("#results img").count() == 0
    assert page.evaluate("window.injected === undefined")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.unroute("**/audit")
    page.route("**/audit", lambda route: route.abort())
    page.locator("#submit").click()
    expect(page.locator("#placeholder-text")).to_contain_text("Нет соединения")
    expect(page.locator("#submit")).to_be_enabled()
    assert not errors, errors
    browser.close()
    print("Browser checks passed: desktop/mobile, findings/empty, loading/errors, size limit, XSS")
