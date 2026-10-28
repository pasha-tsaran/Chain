"""Use only ZeroGPT's public form and visible report, without hidden endpoints."""
import re
from pathlib import Path

from providers import ProviderError, validate_report

SCORE = re.compile(r'(\d+(?:[.,]\d+)?)%\s*AI GPT', re.IGNORECASE)


def parse_visible_report(body, highlights):
    match = SCORE.search(body)
    if not match:
        raise ProviderError('На странице нет результата проверки. Оценка не засчитана.')
    score, fragments = validate_report(float(match[1].replace(',', '.')), highlights)
    return {'score': score, 'fragments': fragments, 'provider': 'ZeroGPT, бесплатная форма сайта (автоматически)'}


def detect_browser(text, directory=None):
    if not 250 <= len(text) <= 15000:
        raise ProviderError('Бесплатная форма рассчитана на текст от 250 до 15 000 символов. Текст не отправлен.')
    try:
        from playwright.sync_api import sync_playwright, Error, TimeoutError
    except ImportError:
        raise ProviderError('Не установлен Playwright. Выполните «Установить модели.cmd» или перенесите результат вручную.') from None
    try:
        with sync_playwright() as playwright:
            # A fresh, isolated context. No personal browser cookies or accounts.
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            try:
                page = browser.new_page(viewport={'width': 1200, 'height': 900})
                page.set_default_timeout(30000)
                page.goto('https://www.zerogpt.com/', wait_until='domcontentloaded', timeout=45000)
                page.get_by_role('textbox', name='Enter text to check for AI and ChatGPT Plagiarism').fill(text)
                page.get_by_role('button', name='Detect Text', exact=True).click()
                page.wait_for_function(r'() => /\d+(?:[.,]\d+)?%\s*AI GPT/i.test(document.body.innerText)', timeout=90000)
                body = page.locator('body').inner_text()
                # Re-read the form to bind the visible report to exactly our input.
                actual = page.get_by_role('textbox', name='Enter text to check for AI and ChatGPT Plagiarism').input_value()
                if actual != text:
                    raise ProviderError('Текст в форме изменился. Результат не засчитан.')
                highlights = page.locator('mark').all_text_contents()
                result = parse_visible_report(body, highlights)
                if directory:
                    directory = Path(directory)
                    directory.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(directory / 'last-zerogpt-report.png'), full_page=False)
                return result
            finally:
                browser.close()
    except (TimeoutError, Error):
        # CAPTCHA, throttling and page changes require the user; never bypass them.
        raise ProviderError('Бесплатная форма недоступна для автоматической проверки: возможны лимит, CAPTCHA или изменение сайта. Перенесите результат вручную. Оценка не засчитана.') from None
