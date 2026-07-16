#!/usr/bin/env python3
"""End-to-end verification for CandyCast admin/member integration."""

from __future__ import annotations

import json
import os
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


BASE = "http://127.0.0.1:8770"


def opener():
    return build_opener(HTTPCookieProcessor(CookieJar()))


def get(client, path: str) -> tuple[int, str, str]:
    response = client.open(f"{BASE}{path}", timeout=20)
    return response.status, response.geturl(), response.read().decode("utf-8", errors="replace")


def post_form(client, path: str, payload: dict[str, object]) -> tuple[int, str, str]:
    request = Request(
        f"{BASE}{path}",
        data=urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        response = client.open(request, timeout=20)
        return response.status, response.geturl(), response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return exc.code, exc.geturl(), exc.read().decode("utf-8", errors="replace")


def api(client, method: str, path: str, payload: dict[str, object] | None = None):
    request = Request(
        f"{BASE}{path}",
        method=method,
        data=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if method != "GET" else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        response = client.open(request, timeout=20)
        body = json.loads(response.read().decode("utf-8"))
        return response.status, body
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"error": raw}
        return exc.code, body


def assert_status(actual: int, expected: int, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected HTTP {expected}, received {actual}")


def main() -> int:
    admin_password = os.environ.get("CANDYCAST_TEST_ADMIN_PASSWORD", "")
    if not admin_password:
        raise SystemExit("Set CANDYCAST_TEST_ADMIN_PASSWORD before running this verifier.")
    suffix = f"{int(time.time()) % 1000000:06d}"
    member_id = f"cc{suffix}"
    duplicate_id = f"du{suffix}"
    signup_code = f"aB{suffix}"
    admin = opener()

    status, url, _ = post_form(
        admin,
        "/bbs/login_check.php",
        {"mb_id": "admin", "mb_password": admin_password, "url": "/admin/members"},
    )
    assert_status(status, 200, "admin login")
    assert url.endswith("/admin/members"), f"admin target mismatch: {url}"

    member_page = ""
    for path, marker in (
        ("/admin/members", "회원 개인정보/등급 관리"),
        ("/admin/partners", "가입코드 발급"),
        ("/admin/chats", "개인 채팅"),
        ("/assets/local/candycast-admin-members.css", ".cc-admin-members"),
        ("/assets/local/candycast-admin-member-chat.js", "POLL_INTERVAL_MS"),
    ):
        status, _, body = get(admin, path)
        assert_status(status, 200, path)
        assert marker in body, f"missing marker in {path}: {marker}"
        if path == "/admin/members":
            member_page = body

    assert 'id="signup-codes"' not in member_page, "signup-code panel remained on member page"
    status, old_code_url, _ = get(admin, "/admin/regist_code.php")
    assert_status(status, 200, "legacy signup-code redirect")
    assert old_code_url.endswith("/admin/partners"), old_code_url

    for invalid_code in ("a1", "ab-"):
        status, _ = api(
            admin,
            "POST",
            "/api/admin/signup-codes/create",
            {"code": invalid_code, "label": "잘못된 형식 검증"},
        )
        assert_status(status, 400, f"invalid signup code {invalid_code}")

    status, created = api(
        admin,
        "POST",
        "/api/admin/signup-codes/create",
        {"code": signup_code, "label": "자동 검증용"},
    )
    assert_status(status, 201, "signup code create")
    assert created.get("code") == signup_code

    visitor = opener()
    status, _, register_html = get(visitor, "/bbs/register_form.php")
    assert_status(status, 200, "registration page")
    assert "가입코드(Sign-up Code)" in register_html
    assert 'pattern="(?=.*[A-Za-z])(?=.*[0-9])(?=.*[^A-Za-z0-9]).{8,15}"' in register_html

    registration = {
        "mb_id": member_id,
        "mb_password": "Test!2345",
        "mb_password_re": "Test!2345",
        "mb_name": "검증회원",
        "mb_nick": "검증닉",
        "mb_hp": "01012345678",
        "chuchu": signup_code,
        "birthy": "1995",
        "birthm": "7",
        "birthd": "16",
        "mb_sex": "M",
    }
    status, url, registration_body = post_form(visitor, "/bbs/register_form_update.php", registration)
    assert_status(status, 200, "member registration")
    assert url.endswith("/bbs/login.php") and "?" not in url, f"registration target mismatch: {url}"
    assert "회원가입이 완료되었습니다." in registration_body

    second_member = opener()
    second_registration = dict(registration, mb_id=duplicate_id, mb_nick="검증닉투")
    status, url, second_body = post_form(second_member, "/bbs/register_form_update.php", second_registration)
    assert_status(status, 200, "reusable signup code")
    assert url.endswith("/bbs/login.php") and "?" not in url, f"second registration target mismatch: {url}"
    assert "회원가입이 완료되었습니다." in second_body

    status, code_payload = api(admin, "GET", "/api/admin/signup-codes")
    assert_status(status, 200, "reusable signup code state")
    reusable_code = next(item for item in code_payload["codes"] if item["code"] == signup_code)
    assert reusable_code["active"] is True and reusable_code["useCount"] == 2

    def update_member(balance_status="정상", account_status="정상", candy=1234, password=""):
        return api(
            admin,
            "POST",
            "/api/admin/members/update",
            {
                "originalId": member_id,
                "id": member_id,
                "password": password,
                "nickname": "검증닉",
                "phone": "01012345678",
                "name": "검증회원",
                "role": "STAFF",
                "displayGrade": "골드",
                "internalGrade": 4,
                "candy": candy,
                "balanceStatus": balance_status,
                "accountStatus": account_status,
            },
        )

    status, _ = update_member(password="Changed!234")
    assert_status(status, 200, "member update and password reset")

    member = opener()
    status, _, _ = post_form(
        member,
        "/bbs/login_check.php",
        {"mb_id": member_id, "mb_password": "Changed!234", "url": "/"},
    )
    assert_status(status, 200, "member login after password reset")

    status, _ = update_member(balance_status="잔고동결")
    assert_status(status, 200, "balance freeze")
    status, _, home = get(member, "/")
    assert_status(status, 200, "frozen member home")
    assert 'data-balance-status="잔고동결"' in home
    status, _, _ = post_form(member, "/bbs/formdata.php", {"req": "export", "price": 1, "count": 1})
    assert_status(status, 423, "frozen balance transaction")

    status, _ = update_member(account_status="계정동결")
    assert_status(status, 200, "account freeze")
    status, _, _ = post_form(member, "/chat/memo_form.php?me_recv_mb_id=temporary", {"message": "차단 검증"})
    assert_status(status, 423, "frozen account chat")

    status, _ = update_member()
    assert_status(status, 200, "member unfreeze")

    status, influencer_payload = api(admin, "GET", "/api/admin/influencers")
    assert_status(status, 200, "influencer list")
    influencers = influencer_payload.get("influencers", [])
    assert influencers, "influencer list is empty"
    influencer = influencers[0]

    gift_message = f"선물 검증 메시지 {suffix}"
    status, gift = api(
        admin,
        "POST",
        "/api/admin/gifts",
        {
            "memberId": member_id,
            "influencerId": influencer["id"],
            "message": gift_message,
            "amount": 321,
        },
    )
    assert_status(status, 201, "candy gift")
    assert gift.get("balance") == 1555, gift

    status, rooms = api(member, "GET", "/api/member/chats")
    assert_status(status, 200, "member private chat list")
    assert any(room.get("id") == influencer["id"] and room.get("lastMessage") == gift_message for room in rooms.get("rooms", []))

    admin_message = f"관리자 BJ 대리 메시지 {suffix}"
    status, _ = api(
        admin,
        "POST",
        "/api/admin/member-chat/messages",
        {"memberId": member_id, "influencerId": influencer["id"], "message": admin_message},
    )
    assert_status(status, 201, "admin influencer message")

    status, _, _ = post_form(
        member,
        f"/chat/memo_form.php?me_recv_mb_id={influencer['id']}",
        {"message": f"회원 답장 {suffix}"},
    )
    assert_status(status, 200, "member reply")

    status, conversation = api(
        admin,
        "GET",
        f"/api/admin/member-chat/messages?member_id={member_id}&influencer_id={influencer['id']}",
    )
    assert_status(status, 200, "admin conversation")
    texts = [message.get("message") for message in conversation.get("messages", [])]
    assert gift_message in texts and admin_message in texts and f"회원 답장 {suffix}" in texts

    status, members = api(admin, "GET", f"/api/admin/members?q={member_id}")
    assert_status(status, 200, "final member state")
    row = next(item for item in members["members"] if item["id"] == member_id)
    assert row["role"] == "STAFF"
    assert row["displayGrade"] == "골드"
    assert row["internalGrade"] == 4
    assert row["candy"] == 1555
    assert row["balanceStatus"] == "정상" and row["accountStatus"] == "정상"
    assert row["online"] is True

    print(
        json.dumps(
            {
                "ok": True,
                "member": member_id,
                "signupCode": signup_code,
                "influencer": influencer["id"],
                "finalCandy": row["candy"],
                "checks": 39,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
