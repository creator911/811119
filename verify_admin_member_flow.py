#!/usr/bin/env python3
"""End-to-end verification for CandyCast admin/member integration."""

from __future__ import annotations

import argparse
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
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
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
    assert 'id="cc-transaction-section"' in member_page, "transaction panel missing on member page"
    assert "/assets/local/candycast-admin-members.js?v=20260719-bj1" in member_page
    status, transaction_page = api(admin, "GET", "/api/admin/transactions?page=1&per_page=10")
    assert_status(status, 200, "admin transaction list")
    assert transaction_page.get("perPage") == 10 and transaction_page.get("page") == 1
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

    status, _, transaction_body = post_form(
        member,
        "/bbs/formdata.php",
        {
            "req": "export",
            "name": "검증회원",
            "bank": "기업은행",
            "bankno": "1234567890",
            "price": 100,
        },
    )
    assert_status(status, 200, "member exchange request")
    transaction_id = int(transaction_body.strip())
    assert transaction_id > 0

    def member_candy() -> int:
        member_status, member_payload = api(admin, "GET", f"/api/admin/members?q={member_id}")
        assert_status(member_status, 200, "member candy lookup")
        return int(next(item for item in member_payload["members"] if item["id"] == member_id)["candy"])

    assert member_candy() == 1134
    status, _, exchange_admin = get(admin, "/admin/export_list.php")
    assert_status(status, 200, "exchange admin page")
    assert "admin.js" in exchange_admin
    assert "/admin/transaction_status.php" in exchange_admin
    assert f'value="{transaction_id}"' in exchange_admin
    token_status, _, token_body = post_form(admin, "/admin/ajax.token.php", {})
    assert_status(token_status, 200, "admin csrf compatibility token")
    assert json.loads(token_body).get("token")

    def set_exchange_status(value: str) -> None:
        exchange_status, exchange_url, _ = post_form(
            admin,
            "/admin/transaction_status.php",
            {"id": transaction_id, "kind": "export", "status": value},
        )
        assert_status(exchange_status, 200, f"exchange status {value}")
        assert exchange_url.endswith("/admin/export_list.php")

    set_exchange_status("동결")
    assert member_candy() == 1234
    set_exchange_status("취소")
    assert member_candy() == 1234
    set_exchange_status("승인")
    assert member_candy() == 1134
    set_exchange_status("취소")
    assert member_candy() == 1234

    status, influencer_payload = api(admin, "GET", "/api/admin/influencers")
    assert_status(status, 200, "influencer list")
    influencers = influencer_payload.get("influencers", [])
    assert len(influencers) >= 2, "at least two influencers are required"
    influencer = influencers[0]
    second_influencer = influencers[1]

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
    status, admin_send = api(
        admin,
        "POST",
        "/api/admin/member-chat/messages",
        {"memberId": member_id, "influencerId": influencer["id"], "message": admin_message},
    )
    assert_status(status, 201, "admin influencer message")
    assert admin_send.get("duplicate") is False
    status, admin_duplicate = api(
        admin,
        "POST",
        "/api/admin/member-chat/messages",
        {"memberId": member_id, "influencerId": influencer["id"], "message": admin_message},
    )
    assert_status(status, 201, "admin duplicate influencer message")
    assert admin_duplicate.get("duplicate") is True
    assert admin_duplicate.get("id") == admin_send.get("id")

    status, _, _ = post_form(
        member,
        f"/chat/memo_form.php?me_recv_mb_id={influencer['id']}",
        {"message": f"회원 답장 {suffix}"},
    )
    assert_status(status, 200, "member reply")

    shared_message = f"다중 BJ 방 분리 {suffix}"
    status, first_send = api(
        member,
        "POST",
        "/api/member/chat/messages",
        {"influencerId": influencer["id"], "message": shared_message},
    )
    assert_status(status, 201, "member first deduplicated send")
    assert first_send.get("duplicate") is False
    status, duplicate_send = api(
        member,
        "POST",
        "/api/member/chat/messages",
        {"influencerId": influencer["id"], "message": shared_message},
    )
    assert_status(status, 201, "member duplicate send")
    assert duplicate_send.get("duplicate") is True
    assert duplicate_send.get("id") == first_send.get("id")

    status, _, _ = get(
        member,
        f"/chat/memo_form.php?me_recv_mb_id={second_influencer['id']}",
    )
    assert_status(status, 200, "member opens empty private room")
    status, empty_rooms = api(member, "GET", "/api/member/chats")
    assert_status(status, 200, "member retains empty private room")
    assert any(room.get("id") == second_influencer["id"] for room in empty_rooms.get("rooms", []))
    status, empty_conversation = api(
        member,
        "GET",
        f"/api/member/chat?influencer_id={second_influencer['id']}",
    )
    assert_status(status, 200, "member empty private conversation")
    assert empty_conversation.get("messages") == []
    status, admin_rooms_before_message = api(
        admin,
        "GET",
        f"/api/admin/member-chat/rooms?q={member_id}",
    )
    assert_status(status, 200, "admin room list before first message")
    assert not any(
        room.get("memberId") == member_id
        and room.get("influencer", {}).get("id") == second_influencer["id"]
        for room in admin_rooms_before_message.get("rooms", [])
    )

    tiny_png = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZJ9sAAAAASUVORK5CYII="
    )
    status, second_send = api(
        member,
        "POST",
        "/api/member/chat/messages",
        {
            "influencerId": second_influencer["id"],
            "message": shared_message,
            "attachment": {"name": "chat.png", "type": "image/png", "data": tiny_png},
        },
    )
    assert_status(status, 201, "member second influencer image send")
    assert second_send.get("duplicate") is False

    status, rooms = api(member, "GET", "/api/member/chats")
    assert_status(status, 200, "member multi-influencer room list")
    room_ids = {room.get("id") for room in rooms.get("rooms", [])}
    assert {influencer["id"], second_influencer["id"]}.issubset(room_ids)

    status, conversation = api(
        admin,
        "GET",
        f"/api/admin/member-chat/messages?member_id={member_id}&influencer_id={influencer['id']}",
    )
    assert_status(status, 200, "admin conversation")
    texts = [message.get("message") for message in conversation.get("messages", [])]
    assert gift_message in texts and f"회원 답장 {suffix}" in texts
    assert texts.count(admin_message) == 1, texts
    assert texts.count(shared_message) == 1, texts

    status, second_conversation = api(
        admin,
        "GET",
        f"/api/admin/member-chat/messages?member_id={member_id}&influencer_id={second_influencer['id']}",
    )
    assert_status(status, 200, "admin second influencer conversation")
    second_messages = second_conversation.get("messages", [])
    assert len([item for item in second_messages if item.get("message") == shared_message]) == 1
    assert any((item.get("attachment") or {}).get("type") == "image/png" for item in second_messages)

    status, admin_rooms = api(admin, "GET", f"/api/admin/member-chat/rooms?q={member_id}")
    assert_status(status, 200, "admin multi-influencer room list")
    admin_room_keys = {
        (room.get("memberId"), room.get("influencer", {}).get("id"))
        for room in admin_rooms.get("rooms", [])
    }
    assert {
        (member_id, influencer["id"]),
        (member_id, second_influencer["id"]),
    }.issubset(admin_room_keys)

    edited_private_message = f"관리자 수정 개인채팅 {suffix}"
    status, edited = api(
        admin,
        "POST",
        "/api/admin/member-chat/messages/edit",
        {
            "id": first_send["id"],
            "memberId": member_id,
            "influencerId": influencer["id"],
            "message": edited_private_message,
        },
    )
    assert_status(status, 200, "admin private message edit")
    assert edited.get("editedAt")

    status, denied_delete = api(
        member,
        "POST",
        "/api/member/chat/messages/delete",
        {"id": admin_send["id"], "influencerId": influencer["id"]},
    )
    assert_status(status, 404, "member cannot delete influencer message")
    assert "찾을 수 없습니다" in denied_delete.get("error", "")

    status, _ = api(
        member,
        "POST",
        "/api/member/chat/messages/delete",
        {"id": first_send["id"], "influencerId": influencer["id"]},
    )
    assert_status(status, 200, "member private message soft delete")
    status, conversation = api(
        admin,
        "GET",
        f"/api/admin/member-chat/messages?member_id={member_id}&influencer_id={influencer['id']}",
    )
    assert_status(status, 200, "private soft delete visible to admin")
    deleted_private = next(item for item in conversation["messages"] if item["id"] == first_send["id"])
    assert deleted_private["deletedByMember"] is True
    assert deleted_private["message"] == "" and deleted_private["attachment"] is None

    status, _ = api(
        admin,
        "POST",
        "/api/admin/member-chat/messages/delete",
        {
            "id": first_send["id"],
            "memberId": member_id,
            "influencerId": influencer["id"],
        },
    )
    assert_status(status, 200, "admin private message hard delete")

    edited_admin_message = f"관리자 BJ 수정 완료 {suffix}"
    status, _ = api(
        admin,
        "POST",
        "/api/admin/member-chat/messages/edit",
        {
            "id": admin_send["id"],
            "memberId": member_id,
            "influencerId": influencer["id"],
            "message": edited_admin_message,
        },
    )
    assert_status(status, 200, "admin influencer message edit")
    status, latest_admin_send = api(
        admin,
        "POST",
        "/api/admin/member-chat/messages",
        {
            "memberId": member_id,
            "influencerId": influencer["id"],
            "message": f"미리보기 수정 전 {suffix}",
        },
    )
    assert_status(status, 201, "latest influencer message send")
    status, _ = api(
        admin,
        "POST",
        "/api/admin/member-chat/messages/edit",
        {
            "id": latest_admin_send["id"],
            "memberId": member_id,
            "influencerId": influencer["id"],
            "message": edited_admin_message,
        },
    )
    assert_status(status, 200, "latest influencer message edit")
    status, room_after_edit = api(member, "GET", "/api/member/chats")
    assert_status(status, 200, "private room preview after edit")
    edited_room = next(room for room in room_after_edit["rooms"] if room["id"] == influencer["id"])
    assert edited_room["lastMessage"] == edited_admin_message

    support_message = f"고객센터 회원 메시지 {suffix}"
    status, support_send = api(
        member,
        "POST",
        "/api/support/messages",
        {"message": support_message},
    )
    assert_status(status, 200, "member support message send")
    status, member_support = api(member, "GET", "/api/support/room?mark_read=1")
    assert_status(status, 200, "member support room")
    support_room_id = member_support["room"]["id"]
    assert any(item["id"] == support_send["id"] for item in member_support["messages"])

    edited_support_message = f"관리자 수정 고객센터 {suffix}"
    status, support_edit = api(
        admin,
        "POST",
        f"/api/admin/support/rooms/{support_room_id}/edit-message",
        {"id": support_send["id"], "message": edited_support_message},
    )
    assert_status(status, 200, "admin support message edit")
    assert support_edit.get("editedAt")
    status, member_support = api(member, "GET", "/api/support/room?mark_read=1")
    edited_support = next(item for item in member_support["messages"] if item["id"] == support_send["id"])
    assert edited_support["message"] == edited_support_message and edited_support["editedAt"]

    status, _ = api(
        member,
        "POST",
        "/api/support/messages/delete",
        {"id": support_send["id"]},
    )
    assert_status(status, 200, "member support message soft delete")
    status, admin_support = api(admin, "GET", f"/api/admin/support/rooms/{support_room_id}")
    assert_status(status, 200, "admin support room after member delete")
    deleted_support = next(item for item in admin_support["messages"] if item["id"] == support_send["id"])
    assert deleted_support["deletedByMember"] is True
    assert deleted_support["message"] == "" and deleted_support["attachment"] is None

    status, _ = api(
        admin,
        "POST",
        f"/api/admin/support/rooms/{support_room_id}/delete-message",
        {"id": support_send["id"]},
    )
    assert_status(status, 200, "admin support message hard delete")

    staff_support_message = f"상담사 답변 수정삭제 {suffix}"
    status, staff_support_send = api(
        admin,
        "POST",
        f"/api/admin/support/rooms/{support_room_id}/messages",
        {"message": staff_support_message},
    )
    assert_status(status, 200, "admin support message send")
    status, _ = api(
        member,
        "POST",
        "/api/support/messages/delete",
        {"id": staff_support_send["id"]},
    )
    assert_status(status, 404, "member cannot delete staff support message")
    status, _ = api(
        admin,
        "POST",
        f"/api/admin/support/rooms/{support_room_id}/edit-message",
        {"id": staff_support_send["id"], "message": f"상담사 수정 완료 {suffix}"},
    )
    assert_status(status, 200, "admin staff support message edit")
    status, _ = api(
        admin,
        "POST",
        f"/api/admin/support/rooms/{support_room_id}/delete-message",
        {"id": staff_support_send["id"]},
    )
    assert_status(status, 200, "admin staff support message delete")

    anonymous = opener()
    status, _ = api(
        anonymous,
        "POST",
        "/api/member/chat/messages/delete",
        {"id": 1, "influencerId": influencer["id"]},
    )
    assert_status(status, 401, "anonymous private message delete")
    status, _ = api(
        anonymous,
        "POST",
        f"/api/admin/support/rooms/{support_room_id}/delete-message",
        {"id": 1},
    )
    assert_status(status, 403, "anonymous admin support delete")

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
                "secondInfluencer": second_influencer["id"],
                "finalCandy": row["candy"],
                "checks": 77,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
