#!/usr/bin/env python3
"""End-to-end verification for member profile images."""

from __future__ import annotations

import argparse
import base64
import io
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image

import standalone_pulseutv_server as app


TEST_USERS = ("__profile_test_a__", "__profile_test_b__")
TEST_PASSWORD = "ProfileTest!2345"


def client() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )


def login(opener: urllib.request.OpenerDirector, base_url: str, username: str) -> None:
    body = urllib.parse.urlencode(
        {"mb_id": username, "mb_password": TEST_PASSWORD, "url": "/"}
    ).encode()
    request = urllib.request.Request(f"{base_url}/bbs/login_check.php", data=body)
    with opener.open(request, timeout=15) as response:
        if response.status != 200:
            raise AssertionError(f"login failed: HTTP {response.status}")


def get(
    opener: urllib.request.OpenerDirector, base_url: str, path: str
) -> tuple[int, object, bytes]:
    with opener.open(f"{base_url}{path}", timeout=15) as response:
        return response.status, response.headers, response.read()


def post_json(
    opener: urllib.request.OpenerDirector,
    base_url: str,
    path: str,
    payload: dict[str, object],
) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def image_data_url(
    image_format: str, color: str, size: tuple[int, int] = (420, 240)
) -> str:
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    image.save(output, image_format)
    mime = "jpeg" if image_format == "JPEG" else "png"
    encoded = base64.b64encode(output.getvalue()).decode()
    return f"data:image/{mime};base64,{encoded}"


def seed_users(db_path: Path) -> None:
    grade = app.DISPLAY_GRADES[2]
    with sqlite3.connect(db_path) as db:
        for username in TEST_USERS:
            db.execute("DELETE FROM wallets WHERE member_id=?", (username,))
            db.execute("DELETE FROM users WHERE id=?", (username,))
            salt, digest = app.hash_password(TEST_PASSWORD)
            now = app.now_text()
            db.execute(
                """INSERT INTO users(
                       id,password_salt,password_hash,name,nickname,phone,sex,birthday,
                       balance,signup_code,role,display_grade,internal_grade,
                       balance_status,account_status,profile_image,profile_image_mime,
                       profile_image_updated_at,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    username,
                    salt,
                    digest,
                    "Profile Test",
                    "Profile Test",
                    "01000000000",
                    "M",
                    "2000-01-01",
                    0,
                    "",
                    "MEMBER",
                    grade,
                    1,
                    app.BALANCE_STATUSES[0],
                    app.ACCOUNT_STATUSES[0],
                    b"",
                    "",
                    "",
                    now,
                ),
            )
            db.execute(
                "INSERT INTO wallets(member_id,balance) VALUES(?,?)",
                (username, 12345),
            )
        db.commit()


def cleanup_users(db_path: Path) -> None:
    with sqlite3.connect(db_path) as db:
        for username in TEST_USERS:
            db.execute("DELETE FROM wallets WHERE member_id=?", (username,))
            db.execute("DELETE FROM users WHERE id=?", (username,))
        db.commit()


def verify(base_url: str, db_path: Path) -> None:
    first = client()
    second = client()
    anonymous = client()
    grade = app.DISPLAY_GRADES[2]
    login(first, base_url, TEST_USERS[0])
    login(second, base_url, TEST_USERS[1])

    _, _, body = get(first, base_url, "/")
    soup = BeautifulSoup(body, "html.parser")
    assert soup.select_one(".h-select > a .candycast-grade-badge")["src"] == (
        app.GRADE_BADGE_ASSETS[grade]
    )
    assert soup.select_one(".candycast-member-profile")["src"] == (
        app.PROFILE_FALLBACK_IMAGE
    )

    status, _ = post_json(
        anonymous,
        base_url,
        "/api/member/profile",
        {"image": image_data_url("PNG", "#fc55a8")},
    )
    assert status == 401
    status, _ = post_json(
        first,
        base_url,
        "/api/member/profile",
        {"image": "data:text/plain;base64,SGVsbG8="},
    )
    assert status == 400

    status, payload = post_json(
        first,
        base_url,
        "/api/member/profile",
        {"image": image_data_url("PNG", "#fc55a8")},
    )
    assert status == 200 and payload["ok"] is True
    first_url = str(payload["profileUrl"])
    _, headers, image_bytes = get(first, base_url, first_url)
    profile = Image.open(io.BytesIO(image_bytes))
    assert headers.get_content_type() == "image/webp"
    assert profile.size == (180, 180)

    _, _, body = get(first, base_url, "/my.php")
    soup = BeautifulSoup(body, "html.parser")
    assert soup.select_one(".cc-member-profile-preview")["src"].startswith(
        f"{app.PROFILE_MEDIA_PATH}?v="
    )
    assert soup.select_one("#iconimg")["accept"] == "image/jpeg,image/png"
    assert soup.select_one("#candycast-profile-script") is not None

    status, payload = post_json(
        first,
        base_url,
        "/api/member/profile",
        {"image": image_data_url("JPEG", "#33aaff", (240, 420))},
    )
    assert status == 200 and payload["profileUrl"] != first_url

    _, _, body = get(second, base_url, "/")
    soup = BeautifulSoup(body, "html.parser")
    assert soup.select_one(".candycast-member-profile")["src"] == (
        app.PROFILE_FALLBACK_IMAGE
    )

    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE users SET account_status=? WHERE id=?",
            (app.ACCOUNT_STATUSES[1], TEST_USERS[0]),
        )
        db.commit()
    status, _ = post_json(
        first,
        base_url,
        "/api/member/profile",
        {"image": image_data_url("PNG", "#ffffff")},
    )
    assert status == 423
    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE users SET account_status=? WHERE id=?",
            (app.ACCOUNT_STATUSES[0], TEST_USERS[0]),
        )
        db.commit()

    status, payload = post_json(
        first, base_url, "/api/member/profile/delete", {}
    )
    assert status == 200
    assert payload["profileUrl"] == app.PROFILE_FALLBACK_IMAGE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8770")
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    db_path = Path(args.db).resolve()
    seed_users(db_path)
    try:
        verify(args.base_url.rstrip("/"), db_path)
    finally:
        cleanup_users(db_path)
    print("profile flow: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
