from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open a browser for manual Qianwen login and save Playwright storage state."
    )
    parser.add_argument(
        "--out",
        default=".auth/qwen-storage-state.json",
        help="Output storage state path. Default: .auth/qwen-storage-state.json",
    )
    parser.add_argument(
        "--login-url",
        default="https://www.qianwen.com/",
        help="Initial page URL to open before manual login.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Seconds to wait for manual login before exiting.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Launch browser with UI. Default is true unless --headless is passed.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Launch browser in headless mode. Normally you should not use this for manual login.",
    )
    return parser.parse_args()


def resolve_output(path_value: str) -> Path:
    return Path(path_value).expanduser().resolve()


def wait_for_manual_confirmation(page, timeout_ms: int) -> None:
    page.evaluate(
        """
        ([timeoutSeconds]) => {
          const bannerId = 'qwen2api-login-banner';
          const existing = document.getElementById(bannerId);
          if (existing) existing.remove();
          const banner = document.createElement('div');
          banner.id = bannerId;
          banner.style.position = 'fixed';
          banner.style.top = '16px';
          banner.style.right = '16px';
          banner.style.zIndex = '2147483647';
          banner.style.padding = '12px 16px';
          banner.style.background = 'rgba(17, 24, 39, 0.92)';
          banner.style.color = '#fff';
          banner.style.fontSize = '14px';
          banner.style.borderRadius = '10px';
          banner.style.boxShadow = '0 8px 30px rgba(0, 0, 0, 0.25)';
          banner.style.maxWidth = '360px';
          banner.innerHTML = `
            <div style="font-weight:600;margin-bottom:6px;">qwen2api 登录保存</div>
            <div style="line-height:1.5;">
              请在当前页面完成登录。登录成功后，点击左下角按钮保存状态。<br/>
              超时时间：${timeoutSeconds} 秒
            </div>
            <button id="qwen2api-login-confirm"
              style="margin-top:10px;padding:6px 10px;border:none;border-radius:8px;background:#2563eb;color:#fff;cursor:pointer;">
              我已登录，保存状态
            </button>
          `;
          document.body.appendChild(banner);
          const button = document.getElementById('qwen2api-login-confirm');
          if (button) {
            button.addEventListener('click', () => {
              window.__QWEN2API_LOGIN_DONE__ = true;
              banner.remove();
            });
          }
        }
        """,
        [max(1, timeout_ms // 1000)],
    )
    page.wait_for_function("() => window.__QWEN2API_LOGIN_DONE__ === true", timeout=timeout_ms)


def main() -> int:
    args = parse_args()
    output_path = resolve_output(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headless = args.headless and not args.headed
    timeout_ms = max(1, int(args.timeout * 1000))

    print(f"[login] output: {output_path}")
    print(f"[login] url: {args.login_url}")
    print("[login] 浏览器会打开目标页面。请手动完成登录，然后点击页面上的“我已登录，保存状态”。")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=headless)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(args.login_url, wait_until="domcontentloaded", timeout=60000)
            wait_for_manual_confirmation(page, timeout_ms)
            context.storage_state(path=str(output_path))
            cookie_count = len(context.cookies())
            print(f"[login] saved storage state: {output_path}")
            print(f"[login] cookie count: {cookie_count}")
            if cookie_count == 0:
                print("[login] warning: 当前保存结果里没有 cookie，请确认登录是否真的成功。")
            return 0
        except PlaywrightTimeoutError as error:
            print(f"[login] timed out after {args.timeout} seconds: {error}", file=sys.stderr)
            return 1
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
