#!/usr/bin/env python3
"""Recheck every archived CandyCast route against the local standalone server."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
from pathlib import Path

import requests


OLD_CHAT_SPELLING = "\ucc57\ud305"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8770")
    parser.add_argument("--routes", default="standalone_visual_debug/final/http_crawl.json")
    parser.add_argument("--out", default="standalone_visual_debug/final/http_crawl_after.json")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    source = json.loads(Path(args.routes).read_text(encoding="utf-8"))
    routes: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in source:
        route = item["route"]
        if route not in seen:
            seen.add(route)
            routes.append((item.get("section", ""), route))

    session = requests.Session()
    admin_password = os.environ.get("CANDYCAST_ADMIN_PASSWORD", "")
    if not admin_password:
        raise SystemExit("Set CANDYCAST_ADMIN_PASSWORD before running the route verifier.")
    session.post(
        args.base + "/bbs/login_check.php",
        data={"mb_id": "admin", "mb_password": admin_password, "url": "/"},
        timeout=20,
    )
    cookies = session.cookies.get_dict()

    def check(item: tuple[str, str]) -> dict[str, object]:
        section, route = item
        try:
            response = requests.get(
                args.base + route,
                cookies=cookies,
                timeout=30,
                allow_redirects=True,
            )
            content_type = response.headers.get("content-type", "")
            text = response.text if "html" in content_type.lower() else ""
            return {
                "section": section,
                "route": route,
                "status": response.status_code,
                "final": response.url.removeprefix(args.base),
                "content_type": content_type,
                "bytes": len(response.content),
                "old_domain": "pulseutv.com" in text.lower(),
                "old_chat_spelling": OLD_CHAT_SPELLING in text,
                "blocked_live": "라이브 입장이 불가합니다" in text,
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "section": section,
                "route": route,
                "status": 0,
                "final": "",
                "content_type": "",
                "bytes": 0,
                "old_domain": False,
                "old_chat_spelling": False,
                "blocked_live": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = list(executor.map(check, routes))

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "routes": len(results),
        "status_200": sum(item["status"] == 200 for item in results),
        "errors": sum(bool(item["error"]) for item in results),
        "old_domain": sum(bool(item["old_domain"]) for item in results),
        "old_chat_spelling": sum(bool(item["old_chat_spelling"]) for item in results),
        "blocked_live": sum(bool(item["blocked_live"]) for item in results),
        "non_html": sum("html" not in str(item["content_type"]).lower() for item in results),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return (
        0
        if summary["status_200"] == summary["routes"]
        and not summary["errors"]
        and not summary["old_chat_spelling"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
