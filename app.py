# -*- coding: utf-8 -*-
"""Flask 应用入口。

启动::

    conda activate flask_env
    python app.py                 # http://127.0.0.1:5000
    python app.py --port 8000 --debug
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Windows 控制台默认可能是 GBK，这里尽量切到 UTF-8 以便打印中文日志
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

import config  # noqa: E402
from flask import Flask, jsonify, request  # noqa: E402
from web.api import api  # noqa: E402
from web.pages import pages  # noqa: E402


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.json.ensure_ascii = False
    # 沙盒项目：模板/静态资源随改随生效，避免开发时被浏览器或 Jinja 缓存坑到
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.register_blueprint(api)
    app.register_blueprint(pages)

    @app.errorhandler(404)
    def not_found(err):  # pragma: no cover - 简单兜底
        if request.path.startswith("/api/"):
            return jsonify({"error": "接口不存在", "path": request.path}), 404
        return err, 404

    @app.errorhandler(500)
    def server_error(err):  # pragma: no cover
        if request.path.startswith("/api/"):
            return jsonify({"error": "内部错误", "detail": str(err)}), 500
        return err, 500

    @app.after_request
    def no_cache_api(resp):
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="组合猜序列启发式搜索沙盒")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--debug", action="store_true", default=config.DEBUG)
    args = parser.parse_args()

    config.ensure_dirs()
    print("=" * 68)
    print(" 组合猜序列 · 启发式搜索沙盒")
    print(f" 候选组合 45（55 − 10 删除）｜序列长度 10｜反馈 4 档")
    print(f" http://{args.host}:{args.port}")
    print(f" 文档目录 {config.DOC_DIR}")
    print(f" 文档日志 {'开启' if config.DOC_LOG_ENABLED else '关闭'}"
          f"（逐轮明细 {'开启' if config.DOC_LOG_ROUNDS else '关闭'}）")
    print("=" * 68)
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
