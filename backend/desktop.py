"""Windows desktop entry point; same origin UI/API and one local instance."""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import multiprocessing
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--ui-smoke-test", action="store_true")
    args = parser.parse_args()
    data_dir = (args.data_dir or Path(os.environ["LOCALAPPDATA"]) / "CryptoSignalCopier").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["COPIER_DATA_DIR"] = str(data_dir)
    os.environ["APP_SEED_DEMO_DATA"] = "false"

    # Keep the lock handle alive until the window and backend have both exited.
    import msvcrt
    lock = (data_dir / "instance.lock").open("a+b")
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        ctypes.windll.user32.MessageBoxW(None, "程序已在运行，请查看已打开的窗口。", "Crypto Signal Copier", 0)
        return

    from logging.handlers import RotatingFileHandler
    from app.release import backup_before_upgrade
    backup_before_upgrade(data_dir)
    logging.basicConfig(handlers=[RotatingFileHandler(data_dir / 'desktop.log', maxBytes=5*1024*1024,
                                                   backupCount=3, encoding='utf-8')], level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # HTTP logs must not include credential-bearing query strings or headers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)

    import uvicorn
    from starlette.responses import FileResponse, JSONResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
    from app.main import app, service
    service.uta_runtime.host_authorized=True

    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    frontend = bundle / "frontend" if getattr(sys, "frozen", False) else bundle / "prototype" / "dist" / "client"
    if not (frontend / "index.html").exists():
        raise RuntimeError("前端文件不存在，请先构建 prototype")
    token = secrets.token_urlsafe(32)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"

    class DesktopAuth:
        def __init__(self, application):
            self.application = application

        async def __call__(self, scope, receive, send):
            if scope["type"] not in {"http", "websocket"}:
                return await self.application(scope, receive, send)
            from http.cookies import SimpleCookie
            from urllib.parse import parse_qs
            headers = dict(scope["headers"])
            cookies = SimpleCookie()
            cookies.load(headers.get(b"cookie", b"").decode())
            supplied = cookies.get("copier_session")
            authenticated = supplied is not None and secrets.compare_digest(supplied.value, token)
            request_origin = headers.get(b"origin", b"").decode()
            valid_origin = not request_origin or request_origin == origin
            valid_host = headers.get(b"host", b"").decode() == f"127.0.0.1:{port}"
            if scope["type"] == "http" and scope["path"] == "/desktop/open" and valid_host:
                candidate = parse_qs(scope["query_string"].decode()).get("token", [""])[0]
                if secrets.compare_digest(candidate, token):
                    response = RedirectResponse("/")
                    response.set_cookie("copier_session", token, httponly=True, samesite="strict")
                    return await response(scope, receive, send)
            if not (authenticated and valid_origin and valid_host):
                if scope["type"] == "websocket":
                    return await send({"type": "websocket.close", "code": 1008})
                return await JSONResponse({"detail": "仅允许当前桌面窗口访问"}, status_code=403)(scope, receive, send)
            return await self.application(scope, receive, send)

    app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(frontend / "index.html")

    server = uvicorn.Server(uvicorn.Config(DesktopAuth(app), host="127.0.0.1", port=port,
                                          log_config=None, access_log=False))
    worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 45
        while not server.started:
            if not worker.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("本地服务启动失败，请查看 desktop.log")
            time.sleep(0.1)
        if args.smoke_test:
            request = urllib.request.Request(origin + "/api/health", headers={"Cookie": f"copier_session={token}"})
            with urllib.request.urlopen(request, timeout=10) as response:
                health = json.load(response)
            from urllib.error import HTTPError
            try:
                urllib.request.urlopen(origin + "/api/connections", timeout=5)
                raise RuntimeError("Unauthenticated access was not blocked")
            except HTTPError as exc:
                assert exc.code == 403
            market_request = urllib.request.Request(origin + "/api/market/overview", headers={"Cookie": f"copier_session={token}"})
            with urllib.request.urlopen(market_request, timeout=35) as response:
                market = json.load(response)
            assert len(market["items"]) >= 4
            (data_dir / "smoke-result.json").write_text(json.dumps({"health": health,
                "frontend": frontend.exists(), "unauthenticated_status": 403, "market": market}), encoding="utf-8")
            return

        import webview
        window = webview.create_window("Crypto Signal Copier · 个人桌面版", origin + "/desktop/open?token=" + token,
                                       width=1440, height=960, min_size=(1100, 720))

        def ui_check():
            try:
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    title = window.evaluate_js("document.querySelector('h1')?.textContent || ''")
                    body = window.evaluate_js("document.body.innerText")
                    if title and ("BTC" in body or "行情暂不可用" in body):
                        break
                    time.sleep(0.2)
                result = {"heading": title, "title": window.evaluate_js("document.title"), "market_body": body}
                window.evaluate_js("document.querySelector('[aria-label=\"链接管理\"]').click()")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    body = window.evaluate_js("document.body.innerText")
                    if "Telegram 登录与频道订阅" in body:
                        break
                    time.sleep(0.1)
                result["connections_body"] = body
                window.evaluate_js("document.querySelector('[aria-label=\"Telegram 消息\"]').click()")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    body = window.evaluate_js("document.body.innerText")
                    if "当前监听频道" in body and "桌面实时推送已连接" in body:
                        break
                    time.sleep(0.1)
                result["telegram_inbox_body"] = body
                result["telegram_stream_connected"] = "桌面实时推送已连接" in body
                window.evaluate_js("document.querySelector('[aria-label=\"跟单模拟\"]').click()")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    body = window.evaluate_js("document.body.innerText")
                    if "自定义模拟 ID" in body:
                        break
                    time.sleep(0.1)
                result["paper_setup_ready"] = "自定义模拟 ID" in body and "创建并持续跟单" in body
                (data_dir / "ui-smoke-result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            finally:
                window.destroy()

        webview.start(ui_check if args.ui_smoke_test else None, gui="edgechromium",
                      storage_path=str(data_dir / "webview"), private_mode=True)
    finally:
        server.should_exit = True
        worker.join(timeout=15)
        sock.close()
        lock.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        main()
    except Exception as exc:
        logging.exception("Desktop startup failed")
        if "--smoke-test" not in sys.argv and "--ui-smoke-test" not in sys.argv:
            ctypes.windll.user32.MessageBoxW(None, f"启动失败：{exc}\n\n请查看用户数据目录中的 desktop.log。\n需要 Microsoft Edge WebView2 Runtime。", "Crypto Signal Copier", 16)
        sys.exit(1)
