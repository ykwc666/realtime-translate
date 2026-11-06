"""本机双语字幕页：标准库 http.server，无外部依赖。

  GET /       内联 HTML
  GET /state  subtitle.json（Cache-Control: no-store）

默认绑定 127.0.0.1:8765。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.subtitle import DEFAULT_SUBTITLE_PATH  # noqa: E402

_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>realtime-translate 字幕</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; height: 100%;
    overflow: hidden;
    background: #0f1216;
    color: #fff;
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
  }
  .wrap {
    height: 100%;
    max-height: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    padding: 4vh 4vw 6vh;
    gap: 2.2vh;
  }
  /* hist 在上、当前行贴底：超高时裁掉顶部（最老历史），当前行不被挤出 */
  .current {
    order: 2;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 0.35em;
    min-height: 2.6em;
  }
  .zh, .en {
    font-size: clamp(22px, 3.6vw, 44px);
    line-height: 1.35;
    font-weight: 600;
    letter-spacing: 0.02em;
    word-break: break-word;
  }
  .en { font-weight: 500; opacity: 0.92; }
  .partial { color: #9aa; }
  .final { color: #fff; }
  .hist {
    order: 1;
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    gap: 0.55em;
    opacity: 0.55;
    font-size: calc(clamp(22px, 3.6vw, 44px) * 0.62);
    line-height: 1.3;
  }
  .hist .row { display: flex; flex-direction: column; gap: 0.15em; }
  .hist .en { opacity: 0.9; font-weight: 400; }
</style>
</head>
<body>
  <div class="wrap">
    <div class="current" id="cur">
      <div class="zh" id="curZh"></div>
      <div class="en" id="curEn"></div>
    </div>
    <div class="hist" id="hist"></div>
  </div>
<script>
(function () {
  var lastRaw = "";
  var curZh = document.getElementById("curZh");
  var curEn = document.getElementById("curEn");
  var histEl = document.getElementById("hist");

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /** 与 app.subtitle.render 对齐：仅 final 跳过 last.t；展示旧→新 */
  function render(st) {
    var state = (st && st.state) || "idle";
    var partial = (st && st.partial_zh) || "";
    var last = (st && st.last) || null;
    var history = (st && Array.isArray(st.history)) ? st.history : [];
    var curZh = "", curEn = "";
    if (state === "speaking") {
      curZh = partial; curEn = "";
    } else if (state === "final" && last) {
      curZh = last.zh || ""; curEn = last.en || "";
    }
    var lastT = last && last.t;
    var skipT = (state === "final") ? lastT : null;
    var histRows = [];
    for (var i = 0; i < history.length; i++) {
      var h = history[i] || {};
      if (skipT != null && h.t === skipT) continue;
      histRows.push(h);
    }
    histRows.reverse();
    histRows = histRows.slice(-5);
    return { curZh: curZh, curEn: curEn, histRows: histRows };
  }

  function apply(st) {
    var state = (st && st.state) || "idle";
    var view = render(st || {});
    curZh.textContent = view.curZh;
    curEn.textContent = view.curEn;
    if (state === "idle") {
      curZh.className = "zh";
      curEn.className = "en";
    } else if (state === "speaking") {
      curZh.className = "zh partial";
      curEn.className = "en partial";
    } else {
      curZh.className = "zh final";
      curEn.className = "en final";
    }
    var html = "";
    for (var i = 0; i < view.histRows.length; i++) {
      var h = view.histRows[i] || {};
      html += '<div class="row"><div class="zh">' + esc(h.zh) +
        '</div><div class="en">' + esc(h.en) + '</div></div>';
    }
    histEl.innerHTML = html;
  }

  function tick() {
    fetch("/state", { cache: "no-store" })
      .then(function (r) { return r.text(); })
      .then(function (txt) {
        if (txt === lastRaw) return;
        lastRaw = txt;
        try { apply(JSON.parse(txt || "{}")); }
        catch (e) {}
      })
      .catch(function () {});
  }
  tick();
  setInterval(tick, 150);
})();
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    state_path: Path = DEFAULT_SUBTITLE_PATH

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # 安静：避免刷屏拖慢终端
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            body = _HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/state"):
            try:
                raw = self.state_path.read_text(encoding="utf-8")
            except OSError:
                raw = json.dumps(
                    {
                        "ts": 0,
                        "state": "idle",
                        "partial_zh": "",
                        "last": None,
                        "history": [],
                    },
                    ensure_ascii=False,
                )
            body = raw.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)


def make_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    state_path: Path | None = None,
) -> ThreadingHTTPServer:
    _Handler.state_path = Path(state_path) if state_path else DEFAULT_SUBTITLE_PATH
    return ThreadingHTTPServer((host, port), _Handler)


def serve_in_thread(
    host: str = "127.0.0.1",
    port: int = 8765,
    state_path: Path | None = None,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    httpd = make_server(host, port, state_path)
    t = threading.Thread(target=httpd.serve_forever, name="subtitle-http", daemon=True)
    t.start()
    print(f"[subtitle-http] http://{host}:{port}/  state={_Handler.state_path}")
    return httpd, t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本机双语字幕页")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--state", type=str, default=str(DEFAULT_SUBTITLE_PATH))
    args = ap.parse_args(argv)
    httpd = make_server(args.host, args.port, Path(args.state))
    print(f"[subtitle-http] http://{args.host}:{args.port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
