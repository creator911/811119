#!/usr/bin/env python3
"""Standalone CandyCast server for the detached local site."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import html
import hmac
import io
import ipaddress
import json
import mimetypes
import os
import random
import re
import secrets
import shutil
import sqlite3
import time
from datetime import datetime
from email import policy
from email.parser import BytesParser
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlparse

from PIL import Image, ImageOps, UnidentifiedImageError


ADMIN_ID = "admin"
ADMIN_PREFIX = "/admin"
LEGACY_ADMIN_PREFIX = "/adm"
DEFAULT_BALANCE = 109_824_200
SESSION_COOKIE = "candycast_session"
FLASH_COOKIE = "candycast_flash"
ADMIN_PASSWORD_SALT = b""
ADMIN_PASSWORD_HASH = b""
ACTIVE_SESSIONS: dict[str, str] = {}
ACTIVE_SESSION_SEEN: dict[str, float] = {}
MEMBER_IP_TOUCHES: dict[str, tuple[str, float]] = {}
BANNED_IPS: frozenset[str] = frozenset()
OPERATOR_IMAGE_PATH = "/assets/local/candycast_operator.png"
MEMBER_CHAT_ICON_PATH = "/assets/local/candycast-member-chat.png"
SHOP_SYMBOL_ASSETS = {
    "실버레벨": ("/assets/local/candycast-shop-silver.png", "실버 등급"),
    "골드레벨": ("/assets/local/candycast-shop-gold.png", "골드 등급"),
    "플래티넘레벨": ("/assets/local/candycast-shop-platinum.png", "플래티넘 등급"),
    "프리미엄": ("/assets/local/candycast-shop-premium.png", "프리미엄 등급"),
    "VIP": ("/assets/local/candycast-shop-vip.png", "VIP 등급"),
    "퀵환전": ("/assets/local/candycast-shop-quick-exchange.png", "퀵환전"),
}
GRADE_BADGE_ASSETS = {
    "브론즈": "/assets/local/candycast-grade-bronze.png",
    "실버": "/assets/local/candycast-grade-silver.png",
    "골드": "/assets/local/candycast-grade-gold.png",
    "플래티넘": "/assets/local/candycast-grade-platinum.png",
    "프리미엄": "/assets/local/candycast-grade-premium.png",
    "VIP": "/assets/local/candycast-grade-vip.png",
}
SUPPORT_QUEUES = {"important", "normal", "uda", "bura"}
SUPPORT_MAX_MESSAGE_LENGTH = 1000
SUPPORT_MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
PROFILE_MAX_SOURCE_BYTES = 2 * 1024 * 1024
PROFILE_MAX_SOURCE_PIXELS = 25_000_000
PROFILE_IMAGE_SIZE = 180
PROFILE_FALLBACK_IMAGE = "/img/no_profile.gif"
PROFILE_MEDIA_PATH = "/media/my-profile.webp"
DISPLAY_GRADES = tuple(GRADE_BADGE_ASSETS)
MEMBER_ROLES = {"MEMBER": "회원", "STAFF": "스태프", "OWNER": "운영자"}
BALANCE_STATUSES = ("정상", "잔고동결")
ACCOUNT_STATUSES = ("정상", "계정동결")
MAX_CANDY_BALANCE = 9_999_999_999
LOGIN_ERROR_MESSAGE = "아이디 또는 비밀번호가 올바르지 않습니다."
IP_BAN_MESSAGE = "접속이 제한된 IP입니다. 관리자에게 문의해 주세요."
ACCOUNT_RESTRICTION_MESSAGE = (
    "회원님의 계정에서 비정상적인 이용 내역이 확인되어 계정이 즉시 이용정지 처리되었습니다. "
    "현재 고객센터를 제외한 모든 서비스 이용이 제한된 상태입니다."
)
BALANCE_RESTRICTION_MESSAGE = (
    "비정상적인 거래 내역이 감지되어 회원님의 캔디 잔고가 동결 처리되었습니다. "
    "고객센터로 문의해 주시기 바랍니다."
)


class CandyCastHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/gif", ".gif")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/manifest+json", ".webmanifest")


def query_hash(query: str) -> str:
    import hashlib

    pairs = parse_qsl(query, keep_blank_values=True)
    normalized = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in pairs)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def normalize_ip(value: object) -> str:
    raw = str(value or "").split(",", 1)[0].strip()
    if not raw:
        return ""
    if raw.startswith("::ffff:"):
        raw = raw[7:]
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return ""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped.compressed
    return address.compressed


def refresh_banned_ips(db_path: Path) -> None:
    global BANNED_IPS
    with sqlite3.connect(db_path) as db:
        rows = db.execute("SELECT ip FROM ip_bans").fetchall()
    BANNED_IPS = frozenset(filter(None, (normalize_ip(row[0]) for row in rows)))


def normalize_profile_image(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError("저장할 프로필 이미지를 선택해주세요.")
    match = re.fullmatch(
        r"data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\r\n]+)",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        raise ValueError("JPG 또는 PNG 이미지만 등록할 수 있습니다.")
    try:
        source = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("이미지 데이터가 올바르지 않습니다.") from exc
    if not source:
        raise ValueError("이미지 데이터가 비어 있습니다.")
    if len(source) > PROFILE_MAX_SOURCE_BYTES:
        raise ValueError("압축된 이미지는 2MB 이하만 등록할 수 있습니다.")

    try:
        with Image.open(io.BytesIO(source)) as opened:
            if (opened.format or "").upper() not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("JPG 또는 PNG 이미지만 등록할 수 있습니다.")
            width, height = opened.size
            if width < 1 or height < 1 or width * height > PROFILE_MAX_SOURCE_PIXELS:
                raise ValueError("이미지 해상도가 너무 큽니다.")
            image = ImageOps.exif_transpose(opened)
            has_alpha = image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            )
            image = image.convert("RGBA" if has_alpha else "RGB")
            image = ImageOps.fit(
                image,
                (PROFILE_IMAGE_SIZE, PROFILE_IMAGE_SIZE),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            output = io.BytesIO()
            image.save(output, "WEBP", quality=88, method=6)
    except ValueError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ValueError("손상되었거나 지원하지 않는 이미지입니다.") from exc
    return output.getvalue()


EXPORT_POPUP = """<div class="export-popup-con">
    <div class="f-header">환전신청 <a href="javascript:closeExport()" class="close-icon"><img src="/ftv/images/ico_close.png" width="14" alt="닫기"></a></div>
    <main>
        <div class="w-full bg-f8 p-2">
            <div class="pb-6">
                <div class="flex flex-col">
                    <div class="space-y-4 border-b2 py-4">
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전은행</span>
                            <select id="cbank" class="form-control" style="width:175px;height:29px;">
                                <option value="">은행을 선택하세요</option>
                                <option value="기업은행">기업은행</option>
                                <option value="국민은행">국민은행</option>
                                <option value="NH농협">NH농협</option>
                                <option value="우리은행">우리은행</option>
                                <option value="신한은행">신한은행</option>
                                <option value="하나은행">하나은행</option>
                                <option value="우체국">우체국</option>
                                <option value="새마을금고">새마을금고</option>
                                <option value="부산은행">부산은행</option>
                                <option value="대구은행">대구은행</option>
                            </select>
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">예금주</span>
                            <input id="cname" class="form-control" style="width:175px;height:29px;">
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전계좌</span>
                            <input id="cbankno" class="form-control" style="width:175px;height:29px;">
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전갯수</span>
                            <input id="cprice" class="form-control" type="number" min="1" onkeyup="gogokey()" onchange="gogokey()" style="width:175px;height:29px;">
                        </div>
                    </div>
                    <div class="flex items-center justify-between" style="margin-top:10px;">
                        <span class="text-lg">환전금액</span>
                        <span class="text-lg" id="exporttotalwon">0 원</span>
                    </div>
                    <article class="modal-guide my-5">
                        <ul>
                            <li>★환전안내★: 환전은 등록된 계좌로만 지급됩니다.</li>
                            <li>- 운영 시간 내 순차적으로 처리됩니다.</li>
                        </ul>
                    </article>
                    <button class="bg-black-900-btn" id="exportloun">환전신청</button>
                </div>
            </div>
        </div>
    </main>
</div>
<input type="hidden" id="balance" value="{balance}">
<input type="hidden" id="mb_7" value="">
<form id="form1" action="/bbs/formdata.php" method="post">
    <input type="hidden" name="req" value="export">
    <input type="hidden" name="id" value="admin">
    <input type="hidden" id="example-text-input3" name="name">
    <input type="hidden" id="example-text-input1" name="bank">
    <input type="hidden" id="example-text-input2" name="bankno">
    <input type="hidden" id="mb_cash" name="price">
</form>
<script>
function addThousandSeparator(num) {{ return num.toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ","); }}
function gogokey() {{
    var cont = $("#cprice").val() || 0;
    $("#exporttotalwon").html(addThousandSeparator(parseInt(cont, 10) || 0) + " 원");
}}
$("#exportloun").off("click").on("click", function() {{
    $("#example-text-input3").val($("#cname").val());
    $("#example-text-input1").val($("#cbank").val());
    $("#example-text-input2").val($("#cbankno").val());
    $("#mb_cash").val($("#cprice").val());
    if ($.trim($("#example-text-input1").val()).length === 0) {{ alert("은행을 선택하여주세요."); return false; }}
    if ($.trim($("#example-text-input3").val()).length === 0) {{ alert("예금주를 입력하여주세요."); return false; }}
    if ($.trim($("#example-text-input2").val()).length === 0) {{ alert("환전계좌를 입력하여주세요."); return false; }}
    if ($.trim($("#mb_cash").val()).length === 0 || parseInt($("#mb_cash").val(), 10) <= 0) {{ alert("환전갯수를 입력하여주세요."); return false; }}
    if (parseInt($("#balance").val(), 10) < parseInt($("#mb_cash").val(), 10)) {{ alert("보유하신금액이 부족합니다."); return false; }}
    $.ajax({{
        type: "POST",
        dataType: "text",
        url: "/bbs/formdata.php",
        data: $("#form1").serialize(),
        success: function(data) {{
            var result = parseInt(data, 10);
            if (result > 0) {{
                $("#export-container").css({{display: "none"}});
                if (typeof showModal === "function") showModal("환전신청 되었습니다.");
                else alert("환전신청 되었습니다.");
            }} else {{
                alert("환전신청 처리에 실패했습니다. 관리자에게 문의해주세요.");
            }}
        }},
        error: function() {{ alert("환전신청 처리 중 오류가 발생했습니다."); }}
    }});
}});
</script>"""


EXPORT_POPUP = """<div class="export-popup-con">
    <div class="f-header">환전신청 <a href="javascript:closeExport()" class="close-icon"><img src="/ftv/images/ico_close.png" width="14" alt="닫기"></a></div>
    <main>
        <div class="w-full bg-f8 p-2">
            <div class="pb-6">
                <div class="flex flex-col">
                    <div class="space-y-4 border-b2 py-4">
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전은행</span>
                            <select id="cbank" class="form-control" style="width:175px;height:29px;">
                                <option value="">은행을 선택하세요</option>
                                <option value="기업은행">기업은행</option>
                                <option value="국민은행">국민은행</option>
                                <option value="NH농협">NH농협</option>
                                <option value="우리은행">우리은행</option>
                                <option value="신한은행">신한은행</option>
                                <option value="하나은행">하나은행</option>
                                <option value="우체국">우체국</option>
                                <option value="새마을금고">새마을금고</option>
                                <option value="부산은행">부산은행</option>
                                <option value="대구은행">대구은행</option>
                            </select>
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">예금주</span>
                            <input id="cname" class="form-control" style="width:175px;height:29px;">
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전계좌</span>
                            <input id="cbankno" class="form-control" style="width:175px;height:29px;">
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전개수</span>
                            <input id="cprice" class="form-control" type="number" min="1" onkeyup="gogokey()" onchange="gogokey()" style="width:175px;height:29px;">
                        </div>
                    </div>
                    <div class="flex items-center justify-between" style="margin-top:10px;">
                        <span class="text-lg">환전금액</span>
                        <span class="text-lg" id="exporttotalwon">0 원</span>
                    </div>
                    <article class="modal-guide my-5">
                        <ul>
                            <li>환전 안내: 사전에 등록된 계좌로만 지급됩니다.</li>
                            <li>- 운영 시간 내 순차적으로 처리됩니다.</li>
                        </ul>
                    </article>
                    <button class="bg-black-900-btn" id="exportloun">환전신청</button>
                </div>
            </div>
        </div>
    </main>
</div>
<input type="hidden" id="balance" value="{balance}">
<form id="form1" action="/bbs/formdata.php" method="post">
    <input type="hidden" name="req" value="export">
    <input type="hidden" name="id" value="admin">
    <input type="hidden" id="example-text-input3" name="name">
    <input type="hidden" id="example-text-input1" name="bank">
    <input type="hidden" id="example-text-input2" name="bankno">
    <input type="hidden" id="mb_cash" name="price">
</form>
<script>
function addThousandSeparator(num) {{ return num.toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ","); }}
function gogokey() {{
    var cont = $("#cprice").val() || 0;
    $("#exporttotalwon").html(addThousandSeparator(parseInt(cont, 10) || 0) + " 원");
}}
$("#exportloun").off("click").on("click", function() {{
    $("#example-text-input3").val($("#cname").val());
    $("#example-text-input1").val($("#cbank").val());
    $("#example-text-input2").val($("#cbankno").val());
    $("#mb_cash").val($("#cprice").val());
    if ($.trim($("#example-text-input1").val()).length === 0) {{ alert("은행을 선택하세요"); return false; }}
    if ($.trim($("#example-text-input3").val()).length === 0) {{ alert("예금주를 입력하세요"); return false; }}
    if ($.trim($("#example-text-input2").val()).length === 0) {{ alert("환전계좌를 입력하세요"); return false; }}
    if ($.trim($("#mb_cash").val()).length === 0 || parseInt($("#mb_cash").val(), 10) <= 0) {{ alert("환전개수를 입력하세요"); return false; }}
    if (parseInt($("#balance").val(), 10) < parseInt($("#mb_cash").val(), 10)) {{ alert("보유하신 금액이 부족합니다."); return false; }}
    $.ajax({{
        type: "POST",
        dataType: "text",
        url: "/bbs/formdata.php",
        data: $("#form1").serialize(),
        success: function(data) {{
            var result = parseInt(data, 10);
            if (result > 0) {{
                $("#export-container").css({{display: "none"}});
                if (typeof showModal === "function") showModal("환전신청이 접수되었습니다.");
                else alert("환전신청이 접수되었습니다.");
            }} else {{
                alert("환전신청 처리에 실패했습니다. 관리자에게 문의하세요.");
            }}
        }},
        error: function() {{ alert("환전신청 처리 중 오류가 발생했습니다."); }}
    }});
}});
</script>"""


EXPORT_POPUP = """<div class="export-popup-con">
    <div class="f-header">환전신청 <a href="javascript:closeExport()" class="close-icon"><img src="/ftv/images/ico_close.png" width="14" alt="닫기"></a></div>
    <main>
        <div class="w-full bg-f8 p-2">
            <div class="pb-6">
                <div class="flex flex-col">
                    <div class="space-y-4 border-b2 py-4">
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전은행</span>
                            <select id="cbank" class="form-control" style="width:175px;height:29px;">
                                <option value="">은행을 선택하세요</option>
                                <option value="기업은행">기업은행</option>
                                <option value="국민은행">국민은행</option>
                                <option value="NH농협">NH농협</option>
                                <option value="우리은행">우리은행</option>
                                <option value="신한은행">신한은행</option>
                                <option value="하나은행">하나은행</option>
                                <option value="우체국">우체국</option>
                                <option value="새마을금고">새마을금고</option>
                                <option value="부산은행">부산은행</option>
                                <option value="대구은행">대구은행</option>
                            </select>
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">예금주</span>
                            <input id="cname" class="form-control" style="width:175px;height:29px;">
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전계좌</span>
                            <input id="cbankno" class="form-control" style="width:175px;height:29px;">
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전금액</span>
                            <input id="cprice" class="form-control" type="number" min="1" onkeyup="gogokey()" onchange="gogokey()" style="width:175px;height:29px;">
                        </div>
                    </div>
                    <div class="flex items-center justify-between" style="margin-top:10px;">
                        <span class="text-lg">환전금액</span>
                        <span class="text-lg" id="exporttotalwon">0 원</span>
                    </div>
                    <article class="modal-guide my-5">
                        <ul>
                            <li>환전 안내: 사전에 등록한 계좌로만 지급됩니다.</li>
                            <li>- 운영 시간 내 순차적으로 처리됩니다.</li>
                        </ul>
                    </article>
                    <button class="bg-black-900-btn" id="exportloun">환전신청</button>
                </div>
            </div>
        </div>
    </main>
</div>
<input type="hidden" id="balance" value="{balance}">
<form id="form1" action="/bbs/formdata.php" method="post">
    <input type="hidden" name="req" value="export">
    <input type="hidden" name="id" value="admin">
    <input type="hidden" id="example-text-input3" name="name">
    <input type="hidden" id="example-text-input1" name="bank">
    <input type="hidden" id="example-text-input2" name="bankno">
    <input type="hidden" id="mb_cash" name="price">
</form>
<script>
function addThousandSeparator(num) {{ return num.toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ","); }}
function gogokey() {{
    var cont = $("#cprice").val() || 0;
    $("#exporttotalwon").html(addThousandSeparator(parseInt(cont, 10) || 0) + " 원");
}}
$("#exportloun").off("click").on("click", function() {{
    $("#example-text-input3").val($("#cname").val());
    $("#example-text-input1").val($("#cbank").val());
    $("#example-text-input2").val($("#cbankno").val());
    $("#mb_cash").val($("#cprice").val());
    if ($.trim($("#example-text-input1").val()).length === 0) {{ alert("은행을 선택하세요"); return false; }}
    if ($.trim($("#example-text-input3").val()).length === 0) {{ alert("예금주를 입력하세요"); return false; }}
    if ($.trim($("#example-text-input2").val()).length === 0) {{ alert("환전계좌를 입력하세요"); return false; }}
    if ($.trim($("#mb_cash").val()).length === 0 || parseInt($("#mb_cash").val(), 10) <= 0) {{ alert("환전금액을 입력하세요"); return false; }}
    if (parseInt($("#balance").val(), 10) < parseInt($("#mb_cash").val(), 10)) {{ alert("보유하신 금액이 부족합니다."); return false; }}
    $.ajax({{
        type: "POST",
        dataType: "text",
        url: "/bbs/formdata.php",
        data: $("#form1").serialize(),
        success: function(data) {{
            var result = parseInt(data, 10);
            if (result > 0) {{
                $("#export-container").css({{display: "none"}});
                if (typeof showModal === "function") showModal("환전신청이 접수되었습니다.");
                else alert("환전신청이 접수되었습니다.");
            }} else {{
                alert("환전신청 처리에 실패했습니다. 관리자에게 문의하세요.");
            }}
        }},
        error: function() {{ alert("환전신청 처리 중 오류가 발생했습니다."); }}
    }});
}});
</script>"""


CANDYCAST_EXPORT_POPUP = """<div class="export-popup-con">
    <div class="f-header">
        환전신청
        <a href="javascript:closeExport()" class="close-icon"><img src="/ftv/images/ico_close.png" width="14" alt="닫기"></a>
    </div>
    <main>
        <div class="w-full bg-f8 p-2">
            <div class="pb-6">
                <div class="flex flex-col">
                    <div class="space-y-4 border-b2 py-4">
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전은행</span>
                            <select id="cbank" class="form-control" style="width:175px;height:29px;">
                                <option value="">은행을 선택하세요</option>
                                <option value="기업은행">기업은행</option>
                                <option value="국민은행">국민은행</option>
                                <option value="NH농협">NH농협</option>
                                <option value="우리은행">우리은행</option>
                                <option value="신한은행">신한은행</option>
                                <option value="하나은행">하나은행</option>
                                <option value="우체국">우체국</option>
                                <option value="외환은행">외환은행</option>
                                <option value="SC제일은행">SC제일은행</option>
                                <option value="새마을금고">새마을금고</option>
                                <option value="한국씨티은행">한국씨티은행</option>
                                <option value="신용협동조합">신용협동조합</option>
                                <option value="제주은행">제주은행</option>
                                <option value="부산은행">부산은행</option>
                                <option value="대구은행">대구은행</option>
                                <option value="광주은행">광주은행</option>
                                <option value="전북은행">전북은행</option>
                                <option value="경남은행">경남은행</option>
                                <option value="산업은행">산업은행</option>
                                <option value="카카오뱅크">카카오뱅크</option>
                                <option value="케이뱅크">케이뱅크</option>
                                <option value="토스뱅크">토스뱅크</option>
                            </select>
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">예금주</span>
                            <input type="text" id="cname" placeholder="이름을 입력해주세요">
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전계좌</span>
                            <input type="text" id="cbankno" inputmode="numeric" placeholder="- 부호 없이 입력해주세요">
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">상품</span>
                            <span class="mr-1.5 text-17">캔디</span>
                        </div>
                        <div class="flex items-center justify-between" style="margin-top:10px;">
                            <span class="text-17 font-medium">환전 캔디</span>
                            <input type="text" id="cprice" inputmode="numeric" placeholder="환전할 캔디 개수를 입력해주세요">
                        </div>
                    </div>
                    <div class="my-3 flex justify-between">
                        <span class="block text-15 font-medium text-ff">예상 환전 금액</span>
                        <span class="text-lg" id="exporttotalwon">0 원</span>
                    </div>
                    <article class="modal-guide my-5">
                        <ul>
                            <li>★환전안내★: 환전은 등록된 계좌로만 지급됩니다.</li>
                            <li>- 사기 및 부정 거래 방지를 위해 본인 확인 절차가 있을 수 있습니다.</li>
                            <li>- 운영 시간 내 순차적으로 처리되며, 주말 및 공휴일은 지연될 수 있습니다.</li>
                        </ul>
                    </article>
                    <button type="button" class="bg-black-900-btn" id="exportloun">환전신청</button>
                </div>
            </div>
        </div>
    </main>
</div>
<input type="hidden" id="balance" value="__BALANCE__">
<form id="form1" action="/bbs/formdata.php" method="post">
    <input type="hidden" name="req" value="export">
    <input type="hidden" name="id" value="__MEMBER_ID__">
    <input type="hidden" id="example-text-input3" name="name">
    <input type="hidden" id="example-text-input1" name="bank">
    <input type="hidden" id="example-text-input2" name="bankno">
    <input type="hidden" id="mb_cash" name="price">
</form>
<script>
function addThousandSeparator(num) {
    return num.toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",");
}
function normalizeDigits(input) {
    input.value = input.value.replace(/[^\\d]/g, "");
}
function gogokey() {
    var count = parseInt($("#cprice").val(), 10) || 0;
    $("#exporttotalwon").text(addThousandSeparator(count) + " 원");
}
$("#cbankno").off("input.candycast").on("input.candycast", function() { normalizeDigits(this); });
$("#cprice").off("input.candycast").on("input.candycast", function() {
    normalizeDigits(this);
    gogokey();
});
$("#exportloun").off("click.candycast").on("click.candycast", function() {
    $("#example-text-input3").val($("#cname").val());
    $("#example-text-input1").val($("#cbank").val());
    $("#example-text-input2").val($("#cbankno").val());
    $("#mb_cash").val($("#cprice").val());

    if ($.trim($("#example-text-input1").val()).length === 0) { alert("은행을 선택하여주세요."); return false; }
    if ($.trim($("#example-text-input3").val()).length === 0) { alert("예금주를 입력하여주세요."); return false; }
    if ($.trim($("#example-text-input2").val()).length === 0) { alert("환전계좌를 입력하여주세요."); return false; }
    if ($.trim($("#mb_cash").val()).length === 0 || parseInt($("#mb_cash").val(), 10) <= 0) { alert("환전할 캔디 개수를 입력하여주세요."); return false; }
    if (parseInt($("#balance").val(), 10) < parseInt($("#mb_cash").val(), 10)) { alert("보유하신 캔디가 부족합니다."); return false; }

    $.ajax({
        type: "POST",
        dataType: "text",
        url: "/bbs/formdata.php",
        data: $("#form1").serialize(),
        success: function(data) {
            if (parseInt(data, 10) > 0) {
                $("#export-container").css({display: "none"});
                if ($("#closeurl").length) $("#closeurl").val("/my2.php");
                if (typeof showModal === "function") showModal("환전신청이 접수되었습니다.");
                else alert("환전신청이 접수되었습니다.");
            } else {
                alert("환전신청 처리에 실패했습니다. 다시 로그인 후 시도해주세요.");
            }
        },
        error: function() { alert("환전신청 처리 중 오류가 발생했습니다."); }
    });
});
</script>"""


LOGGED_IN_PROFILE = """<div class="h-select" data-candycast-auth="member">
    <a href="#n" aria-label="__DISPLAY_NAME__ 회원 메뉴"><img src="__GRADE_IMAGE__" alt="__GRADE_NAME__ 등급" class="candycast-grade-badge"><span class="candycast-profile-name">__DISPLAY_NAME__</span></a>
    <div>
        <h4>
            <p><span>__MEMBER_ID__</span>__DISPLAY_NAME__</p>
            <img src="__PROFILE_IMAGE__" alt="__DISPLAY_NAME__ 프로필" class="candycast-member-profile">
        </h4>
        <li>
            <a href="#n" class="other">보유캔디 <span>__BALANCE__</span></a>
            <a href="/my.php">마이페이지</a>
            <a href="javascript:upexport()">환전하기</a>
        </li>
        <li class="last"><a href="/bbs/logout.php">로그아웃</a></li>
    </div>
</div>"""


LOGGED_OUT_PROFILE = """<div class="h-select2" data-candycast-auth="guest">
    <button type="button" onclick="location.href='/bbs/login.php'">로그인</button>
    <button type="button" onclick="location.href='/bbs/register_form.php'">회원가입</button>
</div>"""


EXCHANGE_NAV_ITEM = """<li data-candycast-auth="exchange">
    <a href="javascript:upexport()"><img src="/ftv/images/ico_do.svg" alt=""> 환전신청</a>
</li>"""


def mobile_navigation_markup(logged_in: bool) -> str:
    my_href = "/my.php" if logged_in else "/bbs/login.php?url=%2Fmy.php"
    return f"""<nav class="cc-mobile-nav" aria-label="모바일 주요 메뉴">
      <a href="/" data-mobile-route="home"><img src="/ftv/images/ico_home.svg" alt=""><span>홈</span></a>
      <a href="/chatlist.php" data-mobile-route="chat"><img src="/ftv/images/ico_vod.svg" alt=""><span>채팅</span></a>
      <a href="/toplank.php" data-mobile-route="ranking"><img src="/ftv/images/ico_rank.svg" alt=""><span>랭킹</span></a>
      <a href="/flex.php" data-mobile-route="shop"><img src="/ftv/images/ico_cart.svg" alt=""><span>아이템샵</span></a>
      <a href="{my_href}" data-mobile-route="my"><img src="/ftv/images/ico_profile_edit.svg" alt=""><span>마이</span></a>
    </nav>"""


MOBILE_MEMBER_ACTIONS = """<nav class="cc-mobile-member-actions" aria-label="회원 빠른 메뉴">
  <a href="/my2.php"><img src="/ftv/images/money_charger_off.svg" alt=""><span>캔디 내역</span></a>
  <button type="button" data-cc-mobile-action="exchange"><img src="/ftv/images/ico_do.svg" alt=""><span>환전신청</span></button>
  <a href="/bbs/board.php?bo_table=notice"><img src="/ftv/images/ico_notice.svg" alt=""><span>공지사항</span></a>
  <a href="/bbs/faq.php"><img src="/ftv/images/event.svg" alt=""><span>자주 묻는 질문</span></a>
  <button type="button" data-cc-mobile-action="support"><img src="/ftv/images/ico_support.svg" alt=""><span>고객센터</span></button>
  <a href="/bbs/logout.php"><img src="/ftv/images/side_menu_off.svg" alt=""><span>로그아웃</span></a>
</nav>"""


EXPORT_SUPPORT = """<div class="export-popup" id="export-container"></div>
<script data-candycast-export="1">
function upexport() {
    $(".h-select > a").removeClass("on");
    $(".h-select > div").hide();
    var container = $("#export-container");
    container.empty().css({display: "flex"});
    $.ajax({
        url: "/export_handler.php",
        method: "GET",
        success: function(data) { container.html(data); },
        error: function() {
            container.html('<div class="export-popup-con"><div class="f-header">환전신청</div><p>로그인 후 이용해주세요.</p></div>');
        }
    });
}
function closeExport() {
    $("#export-container").css({display: "none"}).empty();
}
</script>"""

OPERATOR_AVATAR_STYLE = """
.candycast-operator-avatar {
    object-fit: contain !important;
    object-position: center !important;
    background: #000 !important;
    border-radius: 50% !important;
    box-sizing: border-box !important;
    padding: 0 !important;
}
"""

GRADE_BADGE_STYLE = """
#header .h-select > a {
    display: inline-flex !important;
    align-items: center !important;
    gap: 6px;
}
#header .h-select .candycast-grade-badge {
    display: block !important;
    width: 30px !important;
    height: 30px !important;
    max-width: 30px !important;
    max-height: 30px !important;
    flex: 0 0 30px;
    margin: 0 !important;
    padding: 0 !important;
    border: 0 !important;
    border-radius: 0 !important;
    background: transparent !important;
    object-fit: contain !important;
    object-position: center !important;
    vertical-align: middle !important;
}
#header .h-select .candycast-profile-name {
    display: block;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
#header .h-select h4 .candycast-member-profile {
    display: block !important;
    width: 52px !important;
    height: 52px !important;
    max-width: 52px !important;
    max-height: 52px !important;
    flex: 0 0 52px;
    margin: 0 !important;
    padding: 0 !important;
    border: 0 !important;
    border-radius: 50% !important;
    background: #f0f1f4 !important;
    object-fit: cover !important;
    object-position: center !important;
}
.my-tab1-con .file .cc-member-profile-preview {
    width: 100px !important;
    height: 100px !important;
    border-radius: 50% !important;
    object-fit: cover !important;
    object-position: center !important;
}
"""


PUBLIC_RESPONSIVE_STYLE = OPERATOR_AVATAR_STYLE + GRADE_BADGE_STYLE + """
#footer { left: 0 !important; right: auto !important; width: 100% !important; max-width: 100%; }
#footer .f-menu { width: 100% !important; max-width: 100%; }
@media (max-width: 768px) {
    html, body { min-width: 0 !important; max-width: 100%; overflow-x: hidden; }
    #header { height: 56px; padding: 0 12px; display: flex; align-items: center; justify-content: space-between; }
    #header h1 { float: none; flex: 0 0 auto; }
    #header h1 img { width: 120px; height: 56px; }
    #header .header-con { float: none; height: 56px; min-width: 0; gap: 6px; }
    #header .search { display: none; }
    #header .h-select3 { display: flex; flex: 0 0 auto; }
    #header .h-select3 button { width: auto; min-width: 48px; height: 30px; margin-left: 5px; padding: 0 5px; font-size: 10px; white-space: nowrap; }
    #header .h-select > a { display: flex; width: 36px; height: 36px; padding: 3px; font-size: 0; gap: 0; }
    #header .h-select > a::after { right: -1px; top: 16px; }
    #header .h-select > a > img { width: 30px !important; height: 30px !important; max-width: 30px !important; max-height: 30px !important; margin: 0; vertical-align: 0; }
    #header .h-select > a .candycast-profile-name { display: none; }
    #header .h-select2 { display: flex; }
    #header .h-select2 button { width: auto; min-width: 48px; margin-left: 5px; padding: 0 5px; font-size: 10px; white-space: nowrap; }
    #container { padding-top: 56px; }
    #gnb { display: none !important; }
    #contents, #container.main #contents, #contents.active, #contents.active2, #contents.active3, #contents.active3.active {
        float: none; width: 100% !important; margin-left: 0 !important; padding: 14px 12px 36px !important;
    }
    .topbanner { display: none; }
    .vedios { margin-top: 0; width: 100%; overflow: hidden; }
    .vedios .about { padding: 12px; }
    .vedios .about .img img { width: 38px; height: 38px; }
    .vedios .about .con { min-width: 0; padding-left: 9px; }
    .vedios .about .con strong { display: block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 15px; }
    .vedios .about .con p { font-size: 13px; }
    .main-h3 { margin-top: 22px; font-size: 18px; }
    .tab-con > ul { display: grid !important; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 10px; margin-left: 0 !important; }
    .tab-con > ul > li { float: none !important; width: auto !important; min-width: 0; margin: 0 !important; overflow: visible; }
    .tab-con > ul > li > a { display: block; width: 100%; }
    .tab-con > ul > li > a > img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; }
    .tab-con > ul > li > a ul { left: 6px; top: 6px; }
    .tab-con > ul > li > a ul li { padding: 2px 6px; font-size: 10px; }
    .tab-con .con { min-width: 0; margin-top: 7px; }
    .tab-con .con > a { flex: 0 0 auto; }
    .tab-con .con > div { min-width: 0; }
    .tab-con .con strong, .tab-con .con p, .tab-con .con span { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .maindddd { display: grid; grid-template-columns: 1fr; gap: 10px; overflow: visible !important; }
    .maindddd > div { float: none !important; min-width: 0; }
    .mvdfk { margin-right: 0 !important; }
    .lpko { display: grid; grid-template-columns: 90px minmax(0, 1fr) 54px; }
    .lpko > div { float: none !important; }
    .zxcmnv { width: 90px !important; height: 112px !important; }
    .zxcmnv > img { height: 112px !important; object-fit: cover; }
    .uejdh { width: auto !important; height: 112px !important; min-width: 0; }
    .woiej { width: 54px !important; height: 112px !important; line-height: 112px !important; }
    .table-style2, .tbl_wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
    .table-style2 table, .tbl_wrap table { min-width: 650px; }
    .export-popup-con { width: calc(100vw - 24px) !important; max-width: 420px; max-height: calc(100vh - 24px); overflow-y: auto; }
    .main-popup-box { width: calc(100vw - 24px); max-height: calc(100vh - 24px); overflow-y: auto; padding: 16px; border-radius: 8px; }
    .my2-con { display: grid; grid-template-columns: 1fr; }
    .flex-con ul { display: grid !important; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-left: 0 !important; }
    .flex-con li { float: none !important; width: auto !important; min-width: 0; margin: 0 !important; }
}
"""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ensure_local_assets(site_dir: Path) -> None:
    local_dir = site_dir / "assets" / "local"
    local_dir.mkdir(parents=True, exist_ok=True)
    bundled_assets = Path(__file__).resolve().with_name("candycast_support_assets")
    if bundled_assets.is_dir():
        for asset in bundled_assets.iterdir():
            if asset.is_file():
                shutil.copy2(asset, local_dir / asset.name)
    noop_js = local_dir / "noop.js"
    noop_css = local_dir / "noop.css"
    manifest = local_dir / "manifest.json"
    if not noop_js.exists():
        noop_js.write_text("/* Local standalone placeholder for removed third-party scripts. */\n", encoding="utf-8")
    if not noop_css.exists():
        noop_css.write_text("/* Local standalone placeholder for removed third-party stylesheets. */\n", encoding="utf-8")
    if not manifest.exists():
        manifest.write_text(
            json.dumps(
                {
                    "name": "CandyCast",
                    "short_name": "CandyCast",
                    "start_url": "/",
                    "display": "standalone",
                    "icons": [{"src": "/apple-touch-icon.png", "sizes": "180x180", "type": "image/png"}],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def init_db(db_path: Path, backup_dir: Path | None, site_dir: Path | None = None) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                member_id TEXT NOT NULL,
                name TEXT,
                bank TEXT,
                bankno TEXT,
                price INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT '대기',
                created_at TEXT NOT NULL
            )"""
        )
        transaction_columns = {
            row[1] for row in db.execute("PRAGMA table_info(transactions)").fetchall()
        }
        for column, definition in {
            "count": "INTEGER NOT NULL DEFAULT 0",
            "phone": "TEXT NOT NULL DEFAULT ''",
            "level": "TEXT NOT NULL DEFAULT ''",
            "handled_at": "TEXT NOT NULL DEFAULT ''",
            "handled_by": "TEXT NOT NULL DEFAULT ''",
            "is_deleted": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if column not in transaction_columns:
                db.execute(f"ALTER TABLE transactions ADD COLUMN {column} {definition}")
        db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                name TEXT NOT NULL,
                nickname TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                sex TEXT NOT NULL DEFAULT '',
                birthday TEXT NOT NULL DEFAULT '',
                balance INTEGER NOT NULL DEFAULT 0,
                signup_code TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'MEMBER',
                display_grade TEXT NOT NULL DEFAULT '브론즈',
                internal_grade INTEGER NOT NULL DEFAULT 1,
                balance_status TEXT NOT NULL DEFAULT '정상',
                account_status TEXT NOT NULL DEFAULT '정상',
                profile_image BLOB NOT NULL DEFAULT X'',
                profile_image_mime TEXT NOT NULL DEFAULT '',
                profile_image_updated_at TEXT NOT NULL DEFAULT '',
                last_ip TEXT NOT NULL DEFAULT '',
                last_ip_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )"""
        )
        user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        for column, definition in {
            "signup_code": "TEXT NOT NULL DEFAULT ''",
            "role": "TEXT NOT NULL DEFAULT 'MEMBER'",
            "display_grade": "TEXT NOT NULL DEFAULT '브론즈'",
            "internal_grade": "INTEGER NOT NULL DEFAULT 1",
            "balance_status": "TEXT NOT NULL DEFAULT '정상'",
            "account_status": "TEXT NOT NULL DEFAULT '정상'",
            "profile_image": "BLOB NOT NULL DEFAULT X''",
            "profile_image_mime": "TEXT NOT NULL DEFAULT ''",
            "profile_image_updated_at": "TEXT NOT NULL DEFAULT ''",
            "last_ip": "TEXT NOT NULL DEFAULT ''",
            "last_ip_at": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in user_columns:
                db.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
        db.execute(
            """CREATE TABLE IF NOT EXISTS ip_bans (
                ip TEXT PRIMARY KEY,
                member_id TEXT NOT NULL DEFAULT '',
                memo TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            )"""
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS ip_bans_member_idx ON ip_bans(member_id,created_at DESC)"
        )
        db.execute(
            "UPDATE users SET display_grade='프리미엄' WHERE display_grade='다이아'"
        )
        db.execute(
            "UPDATE users SET display_grade='VIP' WHERE display_grade IN ('마스터','챌린저')"
        )
        grade_placeholders = ",".join("?" for _ in DISPLAY_GRADES)
        db.execute(
            f"UPDATE users SET display_grade=? WHERE display_grade NOT IN ({grade_placeholders})",
            (DISPLAY_GRADES[0], *DISPLAY_GRADES),
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS users_signup_code_idx "
            "ON users(signup_code,created_at DESC)"
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS signup_codes (
                code TEXT PRIMARY KEY,
                label TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                used_by TEXT NOT NULL DEFAULT '',
                used_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )"""
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS signup_codes_active_idx "
            "ON signup_codes(active,used_by,created_at DESC)"
        )
        db.execute(
            "UPDATE signup_codes SET active=1,used_by='',used_at='' WHERE used_by<>''"
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id TEXT NOT NULL,
                receiver_id TEXT NOT NULL,
                member_id TEXT NOT NULL DEFAULT '',
                influencer_id TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                attachment_name TEXT NOT NULL DEFAULT '',
                attachment_type TEXT NOT NULL DEFAULT '',
                attachment_data TEXT NOT NULL DEFAULT '',
                dedupe_key TEXT NOT NULL DEFAULT '',
                created_minute TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                read_at TEXT NOT NULL DEFAULT '',
                edited_at TEXT NOT NULL DEFAULT '',
                edited_by TEXT NOT NULL DEFAULT '',
                deleted_by_member INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        chat_message_columns = {
            row[1] for row in db.execute("PRAGMA table_info(chat_messages)").fetchall()
        }
        for column, definition in {
            "member_id": "TEXT NOT NULL DEFAULT ''",
            "influencer_id": "TEXT NOT NULL DEFAULT ''",
            "attachment_name": "TEXT NOT NULL DEFAULT ''",
            "attachment_type": "TEXT NOT NULL DEFAULT ''",
            "attachment_data": "TEXT NOT NULL DEFAULT ''",
            "dedupe_key": "TEXT NOT NULL DEFAULT ''",
            "created_minute": "TEXT NOT NULL DEFAULT ''",
            "read_at": "TEXT NOT NULL DEFAULT ''",
            "edited_at": "TEXT NOT NULL DEFAULT ''",
            "edited_by": "TEXT NOT NULL DEFAULT ''",
            "deleted_by_member": "INTEGER NOT NULL DEFAULT 0",
            "deleted_at": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in chat_message_columns:
                db.execute(f"ALTER TABLE chat_messages ADD COLUMN {column} {definition}")
        db.execute(
            """CREATE TABLE IF NOT EXISTS member_chat_rooms (
                member_id TEXT NOT NULL,
                influencer_id TEXT NOT NULL,
                last_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(member_id,influencer_id)
            )"""
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS chat_messages_pair_idx "
            "ON chat_messages(sender_id,receiver_id,id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS chat_messages_unread_idx "
            "ON chat_messages(receiver_id,read_at,id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS chat_messages_room_idx "
            "ON chat_messages(member_id,influencer_id,id)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS chat_messages_room_dedupe_idx "
            "ON chat_messages(member_id,influencer_id,sender_id,dedupe_key,created_minute) "
            "WHERE member_id<>'' AND influencer_id<>'' AND dedupe_key<>''"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS member_chat_rooms_member_last_idx "
            "ON member_chat_rooms(member_id,last_at DESC)"
        )
        user_ids = {
            str(row[0]) for row in db.execute("SELECT id FROM users").fetchall()
        }
        influencer_ids: set[str] = set()
        if site_dir is not None:
            influencer_ids = set(member_chat_profiles(site_dir / "chatlist.php.html"))
        unresolved = db.execute(
            """SELECT id,sender_id,receiver_id FROM chat_messages
               WHERE member_id='' AND influencer_id=''
                 AND receiver_id NOT LIKE 'live:%' AND sender_id<>receiver_id"""
        ).fetchall()
        for message_id, sender_id, receiver_id in unresolved:
            sender = str(sender_id)
            receiver = str(receiver_id)
            member_id = ""
            influencer_id = ""
            if sender in user_ids and receiver in influencer_ids:
                member_id, influencer_id = sender, receiver
            elif receiver in user_ids and sender in influencer_ids:
                member_id, influencer_id = receiver, sender
            elif sender in user_ids and receiver not in user_ids:
                member_id, influencer_id = sender, receiver
            elif receiver in user_ids and sender not in user_ids:
                member_id, influencer_id = receiver, sender
            if member_id and influencer_id:
                db.execute(
                    "UPDATE chat_messages SET member_id=?,influencer_id=? WHERE id=?",
                    (member_id, influencer_id, message_id),
                )
        db.execute(
            """DELETE FROM member_chat_rooms
               WHERE member_id NOT IN (SELECT id FROM users)
                 AND influencer_id IN (SELECT id FROM users)"""
        )
        db.execute(
            """INSERT INTO member_chat_rooms(member_id,influencer_id,last_at,created_at)
               SELECT member_id,influencer_id,MAX(created_at),MIN(created_at)
               FROM chat_messages
               WHERE member_id<>'' AND influencer_id<>''
               GROUP BY member_id,influencer_id
               ON CONFLICT(member_id,influencer_id) DO UPDATE SET
                 last_at=MAX(member_chat_rooms.last_at,excluded.last_at)"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS support_rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'open',
                queue TEXT NOT NULL DEFAULT 'normal',
                staff_unread INTEGER NOT NULL DEFAULT 0,
                member_unread INTEGER NOT NULL DEFAULT 0,
                last_message TEXT NOT NULL DEFAULT '',
                last_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS support_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL,
                sender_type TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                attachment_name TEXT NOT NULL DEFAULT '',
                attachment_type TEXT NOT NULL DEFAULT '',
                attachment_data TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                read_at TEXT NOT NULL DEFAULT '',
                edited_at TEXT NOT NULL DEFAULT '',
                edited_by TEXT NOT NULL DEFAULT '',
                deleted_by_member INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(room_id) REFERENCES support_rooms(id) ON DELETE CASCADE
            )"""
        )
        support_message_columns = {
            row[1] for row in db.execute("PRAGMA table_info(support_messages)").fetchall()
        }
        for column, definition in {
            "edited_at": "TEXT NOT NULL DEFAULT ''",
            "edited_by": "TEXT NOT NULL DEFAULT ''",
            "deleted_by_member": "INTEGER NOT NULL DEFAULT 0",
            "deleted_at": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in support_message_columns:
                db.execute(f"ALTER TABLE support_messages ADD COLUMN {column} {definition}")
        db.execute(
            "CREATE INDEX IF NOT EXISTS support_messages_room_id_idx ON support_messages(room_id,id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS support_rooms_queue_updated_idx ON support_rooms(status,queue,updated_at)"
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS wallets (
                member_id TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS candy_gifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id TEXT NOT NULL,
                member_id TEXT NOT NULL,
                influencer_id TEXT NOT NULL,
                message TEXT NOT NULL,
                amount INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS candy_gifts_member_created_idx "
            "ON candy_gifts(member_id,created_at DESC,id DESC)"
        )
        db.execute(
            "INSERT OR IGNORE INTO wallets(member_id,balance) VALUES(?,?)",
            (ADMIN_ID, DEFAULT_BALANCE),
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS imported_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL,
                row_json TEXT NOT NULL
            )"""
        )
        db.commit()


CURRENCY_TEXT_REPLACEMENTS = (
    ("출금", "환전"),
    ("라운지티비", "캔디Cast"),
    ("플라운티비", "캔디Cast"),
    ("보유라운", "보유캔디"),
    ("라운갯수", "캔디 개수"),
    ("라운개수", "캔디 개수"),
    ("라운관리", "캔디관리"),
    ("라운충전", "캔디충전"),
    ("라운을", "캔디를"),
    ("라운 와", "캔디와"),
    ("코인갯수", "캔디 개수"),
    ("코인은", "캔디는"),
    ("코인", "캔디"),
    ("포인트", "캔디"),
    ("캔디갯수", "캔디 개수"),
    ("캔디은", "캔디는"),
)

BRAND_TEXT_REPLACEMENTS = (
    ("펄스tv", "캔디Cast"),
    ("펄스TV", "캔디Cast"),
    ("펄스티비", "캔디Cast"),
    ("PULSEUTV", "CandyCast"),
    ("PulseUTV", "CandyCast"),
    ("pulseutv", "candycast"),
)

UI_TEXT_REPLACEMENTS = (
    ("\ucc57\ud305", "\ucc44\ud305"),
)

ADMIN_BRAND_REPLACEMENTS = (
    ("<h1>\uce94\ub514Cast</h1>", "<h1>Administrator</h1>"),
    ('alt="\uce94\ub514Cast \uad00\ub9ac\uc790"', 'alt="Administrator"'),
    ("alt='\uce94\ub514Cast \uad00\ub9ac\uc790'", "alt='Administrator'"),
)

PROFILE_CLOSE_SCRIPT = (
    '    $(".h-select > a").removeClass("on");\n'
    '    $(".h-select > div").hide();\n'
)

SEARCH_FORM_PATTERN = re.compile(
    r'(<div\s+class=["\']search["\'][^>]*>\s*)'
    r'(<form\b[^>]*\bid=["\']serchfom["\'][^>]*>\s*)'
    r'(<input\b[^>]*>\s*)'
    r'(<a\b[^>]*\bclass=["\']s-btn["\'][^>]*>)'
    r'.*?'
    r'(<img\b[^>]*\bsrc=["\'][^"\']*ico_search\.png[^"\']*["\'][^>]*>)'
    r'.*?</div>',
    re.IGNORECASE | re.DOTALL,
)

EXPIRE_WIDGET_PATTERN = re.compile(
    r"\s*<!--\s*到期日期显示\s*-->.*?</style>\s*"
    r"(?=<li\b[^>]*>\s*<button\b[^>]*\bclass=[\"'][^\"']*tnb_mb_btn[^\"']*[\"'])",
    re.DOTALL,
)


def normalize_search_form(text: str) -> str:
    return SEARCH_FORM_PATTERN.sub(
        lambda match: "".join(match.groups()) + "</a></form></div>",
        text,
    )


def normalize_currency_text(text: str) -> str:
    text = EXPIRE_WIDGET_PATTERN.sub("\n", text)
    text = normalize_search_form(text)
    text = re.sub(r"(?<=\d)ALL\b", " 캔디", text)
    text = re.sub(
        r"보유\s*<span>\s*([\d,]+)\s*<i>F</i>\s*</span>",
        r"보유캔디 <span>\1</span>",
        text,
    )
    text = re.sub(
        r"보유라운\s*:\s*([\d,]+)\s*F(?=<)",
        r"보유캔디 : \1",
        text,
    )
    for old, new in CURRENCY_TEXT_REPLACEMENTS:
        text = text.replace(old, new)
    for old, new in BRAND_TEXT_REPLACEMENTS:
        text = text.replace(old, new)
    for old, new in UI_TEXT_REPLACEMENTS:
        text = text.replace(old, new)
    text = re.sub(r"(?<![가-힣])라운(?![가-힣])", "캔디", text)
    export_opening = "function upexport() {\n"
    if export_opening in text and PROFILE_CLOSE_SCRIPT not in text:
        text = text.replace(export_opening, export_opening + PROFILE_CLOSE_SCRIPT)
    return text


def normalize_currency_tree(root: Path) -> tuple[int, int]:
    changed_files = 0
    total_replacements = 0
    for path in root.rglob("*"):
        if path.suffix.lower() not in {".html", ".php", ".js", ".css"} or not path.is_file():
            continue
        text = read_text(path)
        normalized = normalize_currency_text(text)
        if normalized != text:
            write_text(path, normalized)
            changed_files += 1
            total_replacements += sum(
                text.count(old) for old, _new in CURRENCY_TEXT_REPLACEMENTS
            )
            total_replacements += sum(
                text.count(old) for old, _new in UI_TEXT_REPLACEMENTS
            )
            total_replacements += len(re.findall(r"(?<![가-힣])라운(?![가-힣])", text))
    manifest = root / "detached_manifest.json"
    if manifest.is_file():
        manifest_data = json.loads(read_text(manifest))

        def localize_manifest_urls(value):
            if isinstance(value, dict):
                return {key: localize_manifest_urls(item) for key, item in value.items()}
            if isinstance(value, list):
                return [localize_manifest_urls(item) for item in value]
            if isinstance(value, str):
                return re.sub(
                    r"https?://(?:www\.)?pulseutv\.com",
                    "http://127.0.0.1:8770",
                    value,
                    flags=re.IGNORECASE,
                )
            return value

        localized_manifest = localize_manifest_urls(manifest_data)
        if localized_manifest != manifest_data:
            write_text(
                manifest,
                json.dumps(localized_manifest, ensure_ascii=False, indent=2) + "\n",
            )
            changed_files += 1
    return changed_files, total_replacements


def normalize_admin_brand_tree(root: Path) -> int:
    changed_files = 0
    if not root.is_dir():
        return changed_files
    for path in root.rglob("*"):
        if path.suffix.lower() not in {".html", ".php"} or not path.is_file():
            continue
        text = read_text(path)
        normalized = text
        for old, new in ADMIN_BRAND_REPLACEMENTS:
            normalized = normalized.replace(old, new)
        if normalized != text:
            write_text(path, normalized)
            changed_files += 1
    return changed_files


def prepare_site(source_dir: Path, target_dir: Path) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir)
    replacements = [
        ("url: './offline.html'", "url: '/export_handler.php'"),
        ('url: "./offline.html"', 'url: "/export_handler.php"'),
        ('action="./offline.html"', 'action="/bbs/formdata.php"'),
        ("action='./offline.html'", "action='/bbs/formdata.php'"),
        ('<script src="./offline.html"></script>', '<script src="/assets/local/noop.js"></script>'),
        ('<script src="/offline.html"></script>', '<script src="/assets/local/noop.js"></script>'),
        ('href="./offline.html" rel="stylesheet"', 'href="/assets/local/noop.css" rel="stylesheet"'),
        ('href="/offline.html" rel="stylesheet"', 'href="/assets/local/noop.css" rel="stylesheet"'),
        ('rel="manifest" href="./offline.html"', 'rel="manifest" href="/assets/local/manifest.json"'),
        ('rel="manifest" href="/offline.html"', 'rel="manifest" href="/assets/local/manifest.json"'),
        ('src="./offline.html"', 'src="/img/no_profile.gif"'),
        ('src="/offline.html"', 'src="/img/no_profile.gif"'),
        ('href="./offline.html"', 'href="/offline.html"'),
    ]
    for path in target_dir.rglob("*"):
        if path.suffix.lower() not in {".html", ".php", ".js", ".css"} or not path.is_file():
            continue
        text = read_text(path)
        original = text
        for old, new in replacements:
            text = text.replace(old, new)
        text = normalize_currency_text(text)
        if text != original:
            write_text(path, text)
    normalize_admin_brand_tree(target_dir / "adm")


def canonical_admin_path(path: str) -> str:
    if path == LEGACY_ADMIN_PREFIX:
        return ADMIN_PREFIX
    if path.startswith(f"{LEGACY_ADMIN_PREFIX}/"):
        return f"{ADMIN_PREFIX}{path[len(LEGACY_ADMIN_PREFIX):]}"
    return path


def archived_admin_path(path: str) -> str:
    if path == ADMIN_PREFIX:
        return LEGACY_ADMIN_PREFIX
    if path.startswith(f"{ADMIN_PREFIX}/"):
        return f"{LEGACY_ADMIN_PREFIX}{path[len(ADMIN_PREFIX):]}"
    return path


def is_admin_path(path: str) -> bool:
    return path == ADMIN_PREFIX or path.startswith(f"{ADMIN_PREFIX}/")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_signup_code(value: object) -> str:
    return str(value or "").strip()


def make_flash_cookie(message: str, max_age: int = 120) -> str:
    return (
        f"{FLASH_COOKIE}={quote(message, safe='')}; Path=/; Max-Age={max_age}; "
        "HttpOnly; SameSite=Lax"
    )


def support_widget_markup(logged_in: bool, current_user: str, display_name: str) -> str:
    return f"""<div id="cc-support-root" data-logged-in="{'1' if logged_in else '0'}"
        data-member-id="{html.escape(current_user, quote=True)}"
        data-member-name="{html.escape(display_name, quote=True)}" data-open="false">
      <div class="cc-support-launcher">
        <button type="button" class="cc-support-label" data-cc-support-action="open">상담하기 <span class="cc-support-wave">👋</span></button>
        <button type="button" class="cc-support-fab" data-cc-support-action="toggle" aria-label="고객센터 열기" aria-expanded="false">
          <span class="cc-support-bubble-icon" aria-hidden="true"></span><span class="cc-support-close-icon" aria-hidden="true">×</span>
          <i class="cc-support-unread" hidden>0</i>
        </button>
      </div>
      <section class="cc-support-panel" role="dialog" aria-label="캔디캐스트 고객센터" aria-hidden="true">
        <div class="cc-support-home">
          <div class="cc-support-home-main">
            <img class="cc-support-home-avatar" src="{OPERATOR_IMAGE_PATH}" alt="캔디캐스트 고객센터">
            <button type="button" class="cc-support-home-close" data-cc-support-action="close" aria-label="고객센터 닫기">&times;</button>
            <h2>고객센터</h2>
            <p class="cc-support-home-subtitle">순서대로 상담을 준비중입니다.</p>
            <button type="button" class="cc-support-start" data-cc-support-action="chat">
              <span><strong>채팅하기</strong><small>순서대로 상담을 준비중입니다.</small></span><span class="cc-support-start-icon" aria-hidden="true">➤</span>
            </button>
          </div>
          <nav class="cc-support-home-nav" aria-label="고객센터 메뉴">
            <button type="button" data-cc-support-action="home" aria-current="page"><span aria-hidden="true">⌂</span>홈</button>
            <button type="button" data-cc-support-action="chat"><span aria-hidden="true">▣</span>채팅</button>
          </nav>
          <footer class="cc-support-powered">POWERED BY <strong>CANDY</strong></footer>
        </div>
        <div class="cc-support-chat" hidden>
          <header class="cc-support-chat-head">
            <button type="button" class="cc-support-back" data-cc-support-action="back" aria-label="고객센터 홈으로">‹</button>
            <img src="{OPERATOR_IMAGE_PATH}" alt="캔디캐스트 고객센터">
            <div><strong>고객센터</strong><small>상담원이 답변하면 실시간으로 표시됩니다.</small></div>
            <button type="button" class="cc-support-chat-close" data-cc-support-action="close" aria-label="고객센터 닫기">&times;</button>
          </header>
          <div class="cc-support-login" hidden>
            <strong>로그인이 필요합니다.</strong>
            <p>회원 로그인 후 상담을 시작할 수 있습니다.</p>
            <a href="/bbs/login.php">로그인하기</a>
          </div>
          <div class="cc-support-messages" aria-live="polite"></div>
          <form class="cc-support-composer">
            <input id="cc-support-file" type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden>
            <button type="button" class="cc-support-attach" data-cc-support-action="attach" aria-label="이미지 첨부">＋</button>
            <textarea name="message" maxlength="{SUPPORT_MAX_MESSAGE_LENGTH}" rows="1" placeholder="메시지 입력..." aria-label="상담 메시지"></textarea>
            <button type="submit" class="cc-support-send" aria-label="전송">➤</button>
            <div class="cc-support-attachment-preview" hidden><img alt="첨부 이미지 미리보기"><span></span><button type="button" data-cc-support-action="remove-attachment" aria-label="첨부 취소">×</button></div>
          </form>
        </div>
      </section>
    </div>"""


def member_chat_widget_markup() -> str:
    return f"""<div id="cc-member-chat-root" data-open="false" hidden>
      <button type="button" class="cc-member-chat-launcher" data-cc-member-chat-action="toggle"
        aria-label="개인 채팅 목록 열기" aria-expanded="false">
        <img src="{MEMBER_CHAT_ICON_PATH}" alt=""><i class="cc-member-chat-unread" hidden>0</i>
      </button>
      <section class="cc-member-chat-panel" role="dialog" aria-label="개인 채팅" aria-hidden="true">
        <header class="cc-member-chat-head">
          <div><h2>개인 채팅</h2><p>최근 대화</p></div>
          <button type="button" data-cc-member-chat-action="close" aria-label="개인 채팅 목록 닫기">&times;</button>
        </header>
        <div class="cc-member-chat-list" role="list" aria-live="polite">
          <p class="cc-member-chat-loading">대화 목록을 불러오는 중입니다.</p>
        </div>
        <a class="cc-member-chat-all" href="/chatlist.php">새 채팅 찾아보기</a>
      </section>
    </div>"""


def html_page(title: str, body: str) -> bytes:
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:Arial,'Malgun Gothic',sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:7px;font-size:13px}}th{{background:#f3f4f7}}.nav a{{margin-right:10px}}</style></head>
<body><div class="nav"><a href="/">홈</a><a href="/admin/">관리자</a><a href="/admin/export_list.php">환전신청관리</a><a href="/admin/imported_logs.php">가져온 로그</a></div>{body}</body></html>""".encode("utf-8")


def configure_admin_auth(workdir: Path) -> None:
    global ADMIN_PASSWORD_SALT, ADMIN_PASSWORD_HASH

    salt_hex = os.environ.get("CANDYCAST_ADMIN_PASSWORD_SALT", "").strip()
    hash_hex = os.environ.get("CANDYCAST_ADMIN_PASSWORD_HASH", "").strip()
    if not salt_hex or not hash_hex:
        secrets_path = Path(
            os.environ.get(
                "CANDYCAST_SECRETS_FILE",
                str(workdir / "candycast_secrets.json"),
            )
        )
        if secrets_path.is_file():
            try:
                payload = json.loads(secrets_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("관리자 인증 설정 파일을 읽을 수 없습니다.") from exc
            salt_hex = str(payload.get("admin_password_salt", "")).strip()
            hash_hex = str(payload.get("admin_password_hash", "")).strip()
    try:
        salt = bytes.fromhex(salt_hex)
        digest = bytes.fromhex(hash_hex)
    except ValueError as exc:
        raise RuntimeError("관리자 인증 설정 형식이 올바르지 않습니다.") from exc
    if len(salt) < 16 or len(digest) != 32:
        raise RuntimeError("관리자 인증 설정이 없습니다. 운영 비밀 설정을 먼저 등록해주세요.")
    ADMIN_PASSWORD_SALT = salt
    ADMIN_PASSWORD_HASH = digest


def verify_admin_password(password: str) -> bool:
    if not ADMIN_PASSWORD_SALT or not ADMIN_PASSWORD_HASH:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        ADMIN_PASSWORD_SALT,
        240_000,
    )
    return hmac.compare_digest(candidate, ADMIN_PASSWORD_HASH)


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return salt, digest


def verify_user_password(db_path: Path, username: str, password: str) -> bool:
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT password_salt,password_hash FROM users WHERE id=?",
            (username,),
        ).fetchone()
    if row is None:
        return False
    _salt, candidate = hash_password(password, row[0])
    return hmac.compare_digest(candidate, row[1])


def parse_multipart(content_type: str, body: bytes) -> dict[str, str]:
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
    message = BytesParser(policy=policy.default).parsebytes(header + body)
    values: dict[str, str] = {}
    if not message.is_multipart():
        return values
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        values[name] = payload.decode(charset, errors="ignore")
    return values


def shuffle_direct_children(parent, tag_name: str) -> bool:
    children = [child for child in parent.find_all(tag_name, recursive=False)]
    if len(children) < 2:
        return False
    shuffled = children[:]
    random.shuffle(shuffled)
    for child in children:
        child.extract()
    for child in shuffled:
        parent.append(child)
    return True


def public_page_classes(soup, page_path: Path) -> list[str]:
    name = page_path.name.lower()
    classes = ["cc-public-page"]
    if name in {"index.html", "detached_index.html"}:
        classes.append("cc-page-home")
    if name.startswith("chatlist") or soup.select_one(".maindddd") is not None:
        classes.append("cc-page-chatlist")
    if name.startswith("toplank") or soup.select_one(".grid-container .popular-list-item") is not None:
        classes.append("cc-page-ranking")
    if name.startswith("flex") or soup.select_one(".flex-con") is not None:
        classes.append("cc-page-shop")
    if name.startswith("my.php") or name.startswith("my2.php") or soup.select_one(".my-tab") is not None:
        classes.append("cc-page-my")
    if name.startswith("live__q_"):
        classes.append("cc-page-live")
    if name.startswith("faq") or soup.select_one("#faq_wrap") is not None:
        classes.append("cc-page-faq")
    if name.startswith("board__q_"):
        classes.append("cc-page-board")
    if soup.select_one('form[name="flogin"]') is not None:
        classes.extend(["cc-page-auth", "cc-page-login"])
    if soup.select_one('form[name="fregisterform"]') is not None:
        classes.extend(["cc-page-auth", "cc-page-register"])
    return list(dict.fromkeys(classes))


def optimize_public_images(soup) -> bool:
    changed = False
    priority_images = []
    logo = soup.select_one("#logo img, #header h1 img")
    if logo is not None:
        priority_images.append(logo)
    hero = soup.select_one(".vedios .swiper-slide-active img, .vedios .swiper-slide img")
    if hero is not None:
        priority_images.append(hero)
    priority_ids = {id(image) for image in priority_images}
    for image in soup.find_all("img", src=True):
        if id(image) in priority_ids:
            if image.get("loading") != "eager":
                image["loading"] = "eager"
                changed = True
            if image.get("fetchpriority") != "high":
                image["fetchpriority"] = "high"
                changed = True
        elif image.get("loading") is None:
            image["loading"] = "lazy"
            changed = True
        if image.get("decoding") is None:
            image["decoding"] = "async"
            changed = True
    return changed


def normalize_operator_images(soup) -> bool:
    changed = False
    for image in soup.find_all("img", src=True):
        image_path = urlparse(image["src"]).path.replace("\\", "/").lower()
        is_operator_image = image_path == OPERATOR_IMAGE_PATH or re.search(
            r"(?:^|/)(?:img/10\.jpg|data/member/ad/admin\.gif)$",
            image_path,
        )
        if not is_operator_image:
            continue
        if image.get("src") != OPERATOR_IMAGE_PATH:
            image["src"] = OPERATOR_IMAGE_PATH
            changed = True
        if image.get("alt") != "캔디캐스트 상담원":
            image["alt"] = "캔디캐스트 상담원"
            changed = True
        image_classes = list(image.get("class", []))
        if "candycast-operator-avatar" not in image_classes:
            image_classes.append("candycast-operator-avatar")
            image["class"] = image_classes
            changed = True
    return changed


def render_dynamic_page(
    page_path: Path,
    logged_in: bool = False,
    login_error: bool = False,
    current_user: str = "",
    display_name: str = "",
    balance: int = 0,
    notice: str = "",
    login_target: str = "/",
    balance_status: str = "정상",
    account_status: str = "정상",
    display_grade: str = DISPLAY_GRADES[0],
    profile_image_url: str = PROFILE_FALLBACK_IMAGE,
) -> bytes:
    text = read_text(page_path)
    member_value = "1" if logged_in else ""
    admin_value = "super" if logged_in else ""
    text = re.sub(
        r'(var\s+g5_is_member\s*=\s*)["\'][^"\']*["\']',
        rf'\1"{member_value}"',
        text,
    )
    text = re.sub(
        r'(var\s+g5_is_admin\s*=\s*)["\'][^"\']*["\']',
        rf'\1"{admin_value}"',
        text,
    )
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return text.encode("utf-8")

    soup = BeautifulSoup(text, "html.parser")
    changed = False

    if soup.head is not None:
        viewport = soup.select_one('meta[name="viewport"]')
        if viewport is None:
            viewport = soup.new_tag("meta", attrs={"name": "viewport"})
            viewport["content"] = "width=device-width, initial-scale=1, viewport-fit=cover"
            soup.head.insert(0, viewport)
            changed = True
        elif "viewport-fit=cover" not in viewport.get("content", ""):
            content = viewport.get("content", "width=device-width, initial-scale=1")
            viewport["content"] = f"{content}, viewport-fit=cover"
            changed = True

    page_classes = public_page_classes(soup, page_path)

    if "cc-page-shop" in page_classes:
        for item in soup.select(".flex-con > ul > li"):
            paragraphs = item.find_all("p", recursive=False)
            if len(paragraphs) < 3:
                continue

            symbol_paragraph, product_paragraph, price_paragraph = paragraphs[:3]
            level = product_paragraph.find("b")
            if level is None:
                continue

            product_text = product_paragraph.get_text(" ", strip=True)
            level_text = level.get_text(" ", strip=True)
            if "+" in product_text:
                candy_text = product_text.split("+", 1)[0].strip()
            else:
                candy_text = product_text.removesuffix(level_text).strip()

            symbol_asset = SHOP_SYMBOL_ASSETS.get(level_text)
            symbol_image = symbol_paragraph.find("img")
            if symbol_asset is not None and symbol_image is not None:
                symbol_image["src"] = symbol_asset[0]
                symbol_image["alt"] = symbol_asset[1]
                symbol_image["decoding"] = "async"
                symbol_classes = list(symbol_image.get("class", []))
                if "cc-shop-grade-symbol" not in symbol_classes:
                    symbol_classes.append("cc-shop-grade-symbol")
                symbol_image["class"] = symbol_classes

            for node, class_name in (
                (symbol_paragraph, "cc-shop-symbol"),
                (product_paragraph, "cc-shop-product"),
                (price_paragraph, "cc-shop-price"),
            ):
                node_classes = list(node.get("class", []))
                if class_name not in node_classes:
                    node_classes.append(class_name)
                    node["class"] = node_classes

            product_paragraph.clear()
            candy_label = soup.new_tag("span")
            candy_label["class"] = "cc-shop-candy"
            candy_label.string = candy_text
            level_label = soup.new_tag("b")
            level_label["class"] = "cc-shop-level"
            if level_text == "플래티넘레벨":
                desktop_level = soup.new_tag("span")
                desktop_level["class"] = "cc-shop-level-desktop"
                desktop_level.string = "플래티넘"
                mobile_level = soup.new_tag("span")
                mobile_level["class"] = "cc-shop-level-mobile"
                mobile_level.string = level_text
                level_label.append(desktop_level)
                level_label.append(mobile_level)
            else:
                level_label.string = level_text
            product_paragraph.append(candy_label)
            product_paragraph.append(level_label)
            changed = True

    if soup.body is not None:
        body_classes = list(soup.body.get("class", []))
        for page_class in page_classes:
            if page_class not in body_classes:
                body_classes.append(page_class)
                changed = True
        soup.body["class"] = body_classes
        soup.body["data-cc-authenticated"] = "1" if logged_in else "0"
        if logged_in:
            normalized_grade = display_grade if display_grade in GRADE_BADGE_ASSETS else DISPLAY_GRADES[0]
            soup.body["data-cc-user"] = current_user
            soup.body["data-display-grade"] = normalized_grade
            soup.body["data-balance-status"] = balance_status
            soup.body["data-account-status"] = account_status
            soup.body["data-has-profile-image"] = (
                "1" if profile_image_url != PROFILE_FALLBACK_IMAGE else "0"
            )

    changed = optimize_public_images(soup) or changed

    auth_slot = soup.select_one(".h-select2") or soup.select_one(".h-select")
    if auth_slot is not None:
        normalized_grade = display_grade if display_grade in GRADE_BADGE_ASSETS else DISPLAY_GRADES[0]
        auth_html = LOGGED_IN_PROFILE if logged_in else LOGGED_OUT_PROFILE
        auth_html = auth_html.replace(
            "__MEMBER_ID__", html.escape(current_user or ADMIN_ID, quote=True)
        ).replace(
            "__DISPLAY_NAME__",
            html.escape(display_name or ("상담원" if current_user == ADMIN_ID else current_user), quote=True),
        ).replace("__BALANCE__", f"{balance:,}").replace(
            "__GRADE_NAME__", html.escape(normalized_grade, quote=True)
        ).replace(
            "__GRADE_IMAGE__", html.escape(GRADE_BADGE_ASSETS[normalized_grade], quote=True)
        ).replace(
            "__PROFILE_IMAGE__", html.escape(profile_image_url, quote=True)
        )
        replacement = BeautifulSoup(auth_html, "html.parser").find()
        if replacement is not None:
            auth_slot.replace_with(replacement)
            changed = True

    for broadcast_control in soup.select("#header .h-select3 button, #header .h-select3 a"):
        if broadcast_control.get_text(" ", strip=True).replace(" ", "") == "방송시작":
            broadcast_control.decompose()
            changed = True

    changed = normalize_operator_images(soup) or changed

    live_login_value = "1" if logged_in else ""
    for link in soup.find_all("a", href=True):
        target = link.get("href", "").strip()
        parsed_target = urlparse(target)
        if parsed_target.path != "/live.php" or not parse_qs(parsed_target.query).get("live_id", [""])[0]:
            continue
        link["href"] = (
            "javascript:gogogolink("
            f"{json.dumps(live_login_value)},"
            f"{json.dumps(target, ensure_ascii=False)},"
            "'1')"
        )
        changed = True

    has_public_navigation = False
    for navigation in soup.select("ul.gnb-floor2"):
        has_public_navigation = True
        for link in list(navigation.find_all("a")):
            if link.get_text(" ", strip=True) == "환전신청":
                item = link.find_parent("li")
                if item is not None:
                    item.decompose()
        if logged_in:
            exchange_item = BeautifulSoup(EXCHANGE_NAV_ITEM, "html.parser").find("li")
            itemshop_link = next(
                (
                    link
                    for link in navigation.find_all("a")
                    if "아이템샵" in link.get_text(" ", strip=True)
                ),
                None,
            )
            if exchange_item is not None:
                if itemshop_link is not None and itemshop_link.find_parent("li") is not None:
                    itemshop_link.find_parent("li").insert_after(exchange_item)
                else:
                    navigation.append(exchange_item)
        changed = True

    login_form = soup.select_one('form[name="flogin"]')
    if login_form is not None:
        login_form.attrs.pop("onsubmit", None)
        safe_login_target = login_target or "/"
        if not safe_login_target.startswith("/") or safe_login_target.startswith("//"):
            safe_login_target = "/"
        target_input = login_form.select_one('input[name="url"]')
        if target_input is not None:
            target_input["value"] = quote(safe_login_target, safe="")
        if login_error:
            error = soup.new_tag("p")
            error["class"] = "candycast-login-error"
            error["style"] = "color:#d92853;text-align:center;margin:0 0 12px;font-size:14px;"
            error.string = LOGIN_ERROR_MESSAGE
            login_form.insert_before(error)
        elif notice:
            message = soup.new_tag("p")
            message["class"] = "candycast-login-notice"
            message["style"] = "color:#11875d;text-align:center;margin:0 0 12px;font-size:14px;"
            message.string = notice
            login_form.insert_before(message)
        changed = True

    register_form = soup.select_one('form[name="fregisterform"]')
    if register_form is not None:
        register_form.attrs.pop("onsubmit", None)
        for password_name in ("mb_password", "mb_password_re"):
            password_input = register_form.select_one(f'input[name="{password_name}"]')
            if password_input is None:
                continue
            password_input["required"] = ""
            password_input["minlength"] = "8"
            password_input["maxlength"] = "15"
            password_input["pattern"] = r"(?=.*[A-Za-z])(?=.*[0-9])(?=.*[^A-Za-z0-9]).{8,15}"
            password_input["title"] = "영문, 숫자, 특수문자를 포함한 8~15자"
            password_input["autocomplete"] = "new-password"
        signup_code_input = register_form.select_one('input[name="chuchu"]')
        if signup_code_input is not None:
            signup_code_input["placeholder"] = "가입코드"
            signup_code_input["required"] = ""
            signup_code_input["minlength"] = "3"
            signup_code_input["maxlength"] = "32"
            signup_code_input["pattern"] = "[A-Za-z0-9]{3,32}"
            signup_code_input["title"] = "영문 대소문자와 숫자 3자 이상"
            signup_code_input["autocomplete"] = "off"
            signup_code_row = signup_code_input.find_parent("li")
            signup_code_label = signup_code_row.find("strong") if signup_code_row else None
            if signup_code_label is not None:
                signup_code_label.string = "가입코드(Sign-up Code)"
        if notice:
            message = soup.new_tag("p")
            message["class"] = "candycast-register-notice"
            message["style"] = "color:#d92853;text-align:center;margin:0 0 12px;font-size:14px;"
            message.string = notice
            register_form.insert_before(message)
        changed = True

    if logged_in and page_path.name.startswith("my.php"):
        profile_preview = soup.select_one(".my-tab1-con .file .img1")
        if profile_preview is not None:
            profile_preview["src"] = profile_image_url
            profile_preview["alt"] = f"{display_name or current_user} 프로필"
            profile_preview["class"] = list(profile_preview.get("class", [])) + [
                "cc-member-profile-preview"
            ]
            profile_preview["decoding"] = "async"
            changed = True
        profile_input = soup.select_one("#iconimg")
        if profile_input is not None:
            profile_input["accept"] = "image/jpeg,image/png"
            changed = True
        for link in soup.select(".my-tab1-con a"):
            if link.get_text(" ", strip=True) == "프로필 삭제":
                link["id"] = "cc-profile-delete"
                changed = True
                break
        profile_help = soup.select_one(".file-popup .file-con p")
        if profile_help is not None:
            profile_help.clear()
            profile_help.append("* JPG, PNG / 원본 최대 50MB")
            profile_help.append(soup.new_tag("br"))
            profile_help.append("* 저장 시 중앙 기준 180px × 180px로 자동 조정")
            changed = True
        for row in soup.select(".my-tab1-con > table > tbody > tr"):
            heading = row.find("th")
            cell = row.find("td")
            if heading is None or cell is None:
                continue
            label = heading.get_text(" ", strip=True)
            if label == "ID":
                cell.clear()
                cell.string = current_user
                changed = True
            elif label == "닉네임":
                nickname_input = cell.select_one("#newnick")
                if nickname_input is not None:
                    nickname_input["value"] = display_name or current_user
                    changed = True

    if logged_in and has_public_navigation and soup.body is not None:
        needs_container = soup.select_one("#export-container") is None
        needs_script = "function upexport" not in text
        if needs_container or needs_script:
            support = BeautifulSoup(EXPORT_SUPPORT, "html.parser")
            if needs_container:
                container = support.select_one("#export-container")
                if container is not None:
                    soup.body.append(container.extract())
            if needs_script:
                script = support.select_one('script[data-candycast-export="1"]')
                if script is not None:
                    soup.body.append(script.extract())
            changed = True

    for gnb in soup.select("ul.gnb-floor1"):
        changed = shuffle_direct_children(gnb, "li") or changed
    for wrapper in soup.select(".vedios .swiper-wrapper"):
        changed = shuffle_direct_children(wrapper, "div") or changed
    for live_list in soup.select(".tab1-con > ul"):
        changed = shuffle_direct_children(live_list, "li") or changed
    for chat_list in soup.select(".maindddd"):
        changed = shuffle_direct_children(chat_list, "div") or changed
    if page_path.name.startswith("chatlist"):
        for balance_row in soup.select(".maindddd .jejhdh"):
            if balance_row.get_text(" ", strip=True).startswith("보유캔디"):
                balance_row.decompose()
        for entry in soup.select(".woiej[onclick]"):
            match = re.search(r"openWindow\([^,]*,\s*['\"]([^'\"]+)['\"]\)", entry.get("onclick", ""))
            if match:
                target = f"/chat/memo_form.php?me_recv_mb_id={quote(match.group(1))}"
                entry["onclick"] = f"location.href='{target}'"
                entry["role"] = "button"
                entry["tabindex"] = "0"
        changed = True

    if soup.head is not None and soup.select_one("#candycast-responsive") is None:
        responsive = soup.new_tag("style", id="candycast-responsive")
        responsive.string = PUBLIC_RESPONSIVE_STYLE
        soup.head.append(responsive)
        changed = True

    if soup.head is not None and soup.select_one("#candycast-support-style") is None:
        support_style = soup.new_tag(
            "link",
            id="candycast-support-style",
            rel="stylesheet",
            href="/assets/local/candycast-support.css?v=20260717-chat3",
        )
        soup.head.append(support_style)
        changed = True
    if (
        logged_in
        and soup.head is not None
        and soup.select_one("#candycast-member-chat-style") is None
    ):
        member_chat_style = soup.new_tag(
            "link",
            id="candycast-member-chat-style",
            rel="stylesheet",
            href="/assets/local/candycast-member-chat.css?v=20260717-chat3",
        )
        soup.head.append(member_chat_style)
        changed = True
    if soup.head is not None and soup.select_one("#candycast-mobile-style") is None:
        mobile_style = soup.new_tag(
            "link",
            id="candycast-mobile-style",
            rel="stylesheet",
            href="/assets/local/candycast-mobile.css?v=20260718-ranking2",
        )
        soup.head.append(mobile_style)
        changed = True
    if logged_in and soup.head is not None and soup.select_one("#candycast-restrictions-style") is None:
        restriction_style = soup.new_tag(
            "link",
            id="candycast-restrictions-style",
            rel="stylesheet",
            href="/assets/local/candycast-restrictions.css",
        )
        soup.head.append(restriction_style)
        changed = True

    if logged_in and "cc-page-my" in page_classes and soup.select_one(".cc-mobile-member-actions") is None:
        actions = BeautifulSoup(MOBILE_MEMBER_ACTIONS, "html.parser").find(
            class_="cc-mobile-member-actions"
        )
        if actions is not None and soup.body is not None:
            my_tabs = soup.select_one(".my-tab")
            if my_tabs is not None:
                my_tabs.insert_before(actions)
            else:
                soup.body.append(actions)
            changed = True

    if soup.body is not None and soup.select_one("#cc-support-root") is None:
        widget = BeautifulSoup(
            support_widget_markup(logged_in, current_user, display_name),
            "html.parser",
        ).find(id="cc-support-root")
        if widget is not None:
            soup.body.append(widget)
        image_script = soup.new_tag(
            "script",
            src="/assets/local/candycast-image-utils.js",
            defer=True,
        )
        soup.body.append(image_script)
        support_script = soup.new_tag(
            "script",
            src="/assets/local/candycast-support.js?v=20260717-chat3",
            defer=True,
        )
        soup.body.append(support_script)
        changed = True
    if logged_in and soup.body is not None and soup.select_one("#cc-member-chat-root") is None:
        member_chat_widget = BeautifulSoup(
            member_chat_widget_markup(), "html.parser"
        ).find(id="cc-member-chat-root")
        if member_chat_widget is not None:
            soup.body.append(member_chat_widget)
        member_chat_script = soup.new_tag(
            "script",
            id="candycast-member-chat-script",
            src="/assets/local/candycast-member-chat.js?v=20260717-chat3",
            defer=True,
        )
        soup.body.append(member_chat_script)
        changed = True
    if soup.body is not None and soup.select_one(".cc-mobile-nav") is None:
        mobile_nav = BeautifulSoup(
            mobile_navigation_markup(logged_in), "html.parser"
        ).find(class_="cc-mobile-nav")
        if mobile_nav is not None:
            soup.body.append(mobile_nav)
        changed = True
    if soup.body is not None and soup.select_one("#candycast-mobile-script") is None:
        mobile_script = soup.new_tag(
            "script",
            id="candycast-mobile-script",
            src="/assets/local/candycast-mobile.js?v=20260717-audit1",
            defer=True,
        )
        soup.body.append(mobile_script)
        changed = True
    if logged_in and soup.body is not None and soup.select_one("#candycast-restrictions-script") is None:
        restriction_script = soup.new_tag(
            "script",
            id="candycast-restrictions-script",
            src="/assets/local/candycast-restrictions.js",
            defer=True,
        )
        soup.body.append(restriction_script)
        changed = True
    if (
        logged_in
        and page_path.name.startswith("my.php")
        and soup.body is not None
        and soup.select_one("#candycast-profile-script") is None
    ):
        profile_script = soup.new_tag(
            "script",
            id="candycast-profile-script",
            src="/assets/local/candycast-profile.js",
            defer=True,
        )
        soup.body.append(profile_script)
        changed = True

    if changed and soup.head:
        marker = soup.new_tag("meta")
        marker["name"] = "candycast-local-rendered"
        marker["content"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        soup.head.append(marker)
    return str(soup).encode("utf-8")


def render_dynamic_home(
    index_path: Path,
    logged_in: bool = False,
    current_user: str = "",
    display_name: str = "",
    balance: int = 0,
    balance_status: str = "정상",
    account_status: str = "정상",
    display_grade: str = DISPLAY_GRADES[0],
    profile_image_url: str = PROFILE_FALLBACK_IMAGE,
) -> bytes:
    return render_dynamic_page(
        index_path,
        logged_in=logged_in,
        current_user=current_user,
        display_name=display_name,
        balance=balance,
        balance_status=balance_status,
        account_status=account_status,
        display_grade=display_grade,
        profile_image_url=profile_image_url,
    )


MAINTENANCE_ACTIONS = {
    "session_file_delete.php": (
        "세션파일 일괄삭제",
        "접속 중인 사용자의 로그인이 풀릴 수 있습니다. 진행할까요?",
    ),
    "cache_file_delete.php": (
        "캐시파일 일괄삭제",
        "캐시 파일을 삭제합니다. 필요 시 다시 생성됩니다. 진행할까요?",
    ),
    "captcha_file_delete.php": (
        "캡챠파일 일괄삭제",
        "캡차 임시 파일을 삭제합니다. 진행할까요?",
    ),
    "thumbnail_file_delete.php": (
        "썸네일파일 일괄삭제",
        "썸네일 파일을 삭제합니다. 원본 이미지는 유지됩니다. 진행할까요?",
    ),
}


def normalize_admin_html(text: str) -> str:
    text = normalize_currency_text(text)
    text = text.replace(f"{LEGACY_ADMIN_PREFIX}/", f"{ADMIN_PREFIX}/")
    text = text.replace(f'"{LEGACY_ADMIN_PREFIX}"', f'"{ADMIN_PREFIX}"')
    text = text.replace(f"'{LEGACY_ADMIN_PREFIX}'", f"'{ADMIN_PREFIX}'")
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return text

    soup = BeautifulSoup(text, "html.parser")
    if normalize_operator_images(soup) and soup.head is not None:
        operator_style = soup.new_tag("style", id="candycast-operator-avatar-style")
        operator_style.string = OPERATOR_AVATAR_STYLE
        soup.head.append(operator_style)
    for link in soup.find_all("a"):
        label = link.get_text(" ", strip=True)
        if label == "로그아웃":
            link["href"] = "/bbs/logout.php"
        elif label == "커뮤니티 바로가기":
            link["href"] = "/"
        elif label == "쇼핑몰 바로가기":
            link["href"] = "/flex.php"
        elif label == "관리자정보":
            link["href"] = "/my.php"
        elif label in {"회원관리", "회원개인정보"}:
            link["href"] = f"{ADMIN_PREFIX}/members"
            link.string = "회원개인정보"
        elif label == "파트너추가":
            link["href"] = f"{ADMIN_PREFIX}/partners"
        elif label in {"추천인코드", "가입코드"}:
            item = link.find_parent("li")
            if item is not None:
                item.decompose()
            else:
                link.decompose()
            continue
        elif label in {"채팅방관리", "채팅리스트"}:
            link["href"] = f"{ADMIN_PREFIX}/chats"
        for filename, (button_label, warning) in MAINTENANCE_ACTIONS.items():
            if label == button_label:
                link["href"] = f"{ADMIN_PREFIX}/{filename}"
                link["onclick"] = f"return confirm({json.dumps(warning, ensure_ascii=False)})"
                break
        href = link.get("href", "")
        if href.endswith("export_list.php.html"):
            link["href"] = f"{ADMIN_PREFIX}/export_list.php"
        elif href.endswith("import_list.php.html"):
            link["href"] = f"{ADMIN_PREFIX}/import_list.php"

    logo_link = soup.select_one("#logo a")
    if logo_link is not None:
        logo_link["href"] = f"{ADMIN_PREFIX}/"

    if soup.head is not None and soup.select_one('meta[name="viewport"]') is None:
        viewport = soup.new_tag("meta")
        viewport["name"] = "viewport"
        viewport["content"] = "width=device-width, initial-scale=1"
        soup.head.append(viewport)
    if soup.head is not None and soup.select_one("#candycast-admin-support-style") is None:
        support_style = soup.new_tag(
            "link",
            id="candycast-admin-support-style",
            rel="stylesheet",
            href="/assets/local/candycast-admin-support.css?v=20260717-chat3",
        )
        soup.head.append(support_style)

    tnb = soup.select_one("#tnb > ul")
    if tnb is not None:
        nav_items = (
            (
                "cc-admin-members-nav",
                f'<li class="tnb_li" id="cc-admin-members-nav"><a href="{ADMIN_PREFIX}/members">회원관리</a></li>',
            ),
            (
                "cc-admin-chat-nav",
                f'<li class="tnb_li" id="cc-admin-chat-nav"><a href="{ADMIN_PREFIX}/chats">개인채팅</a></li>',
            ),
            (
                "cc-admin-support-nav",
                f'<li class="tnb_li" id="cc-admin-support-nav"><a href="{ADMIN_PREFIX}/support">고객센터 <i id="cc-admin-support-unread" hidden>0</i></a></li>',
            ),
        )
        for item_id, item_html in reversed(nav_items):
            if soup.select_one(f"#{item_id}") is not None:
                continue
            item = BeautifulSoup(item_html, "html.parser").find("li")
            if item is not None:
                tnb.insert(0, item)

    if soup.body is not None and soup.select_one("#candycast-admin-nav-script") is None:
        support_script = soup.new_tag(
            "script",
            id="candycast-admin-nav-script",
            src="/assets/local/candycast-admin-nav.js",
        )
        soup.body.append(support_script)

    if "jQuery" in text and not any(
        "jquery-1.12.4.min.js" in script.get("src", "") for script in soup.find_all("script")
    ):
        first_script = soup.find("script")
        for source in reversed(
            [
                "/js/jquery-1.12.4.min.js",
                "/js/jquery-migrate-1.4.1.min.js",
                "/js/jquery.menu.js",
                "/js/common.js",
            ]
        ):
            script = soup.new_tag("script", src=source)
            if first_script is not None:
                first_script.insert_before(script)
            elif soup.head is not None:
                soup.head.append(script)
    return str(soup)


def remove_legacy_admin_token_script(soup: object) -> None:
    """Custom API forms do not use the archived PHP CSRF-token endpoint."""
    find_all = getattr(soup, "find_all", None)
    if not callable(find_all):
        return
    for script in find_all("script", src=True):
        source = str(script.get("src", ""))
        if Path(urlparse(source).path).name == "admin.js":
            script.decompose()


SUPPORT_ADMIN_MARKUP = """
<div class="cc-admin-support">
  <header class="cc-support-admin-head">
    <div class="cc-support-admin-title"><h1>고객센터</h1><p>회원 상담을 실시간으로 확인하고 답변합니다.</p></div>
    <div class="cc-support-admin-tools"><span class="cc-support-admin-connection" id="cc-support-admin-connection">연결 확인 중</span><button type="button" class="cc-support-admin-refresh" id="cc-support-admin-refresh">새로고침</button></div>
  </header>
  <div class="cc-support-admin-grid">
    <section class="cc-support-queues" aria-label="상담함">
      <section class="cc-support-queue" data-queue="important"><h2>중요상담함 <span data-queue-count="important">0</span></h2><div class="cc-support-room-list" data-queue-list="important"><p class="cc-support-room-empty">상담방이 없습니다.</p></div></section>
      <section class="cc-support-queue" data-queue="uda"><h2>우다상담함 <span data-queue-count="uda">0</span></h2><div class="cc-support-room-list" data-queue-list="uda"><p class="cc-support-room-empty">상담방이 없습니다.</p></div></section>
      <section class="cc-support-queue" data-queue="normal"><h2>일반상담함 <span data-queue-count="normal">0</span></h2><div class="cc-support-room-list" data-queue-list="normal"><p class="cc-support-room-empty">상담방이 없습니다.</p></div></section>
      <section class="cc-support-queue" data-queue="bura"><h2>부라상담함 <span data-queue-count="bura">0</span></h2><div class="cc-support-room-list" data-queue-list="bura"><p class="cc-support-room-empty">상담방이 없습니다.</p></div></section>
    </section>
    <section class="cc-support-staff-chat" aria-label="상담 대화">
      <header class="cc-support-chat-meta">
        <button type="button" class="cc-support-mobile-back" data-admin-action="mobile-back" aria-label="상담함으로">‹</button>
        <div class="cc-support-chat-person"><strong id="cc-support-room-title">상담방을 선택하세요.</strong><small id="cc-support-room-meta">왼쪽 상담함에서 회원을 선택하세요.</small></div>
        <div class="cc-support-chat-actions"><button type="button" id="cc-support-clear-room" disabled>채팅방 비우기</button><button type="button" id="cc-support-close-room" disabled>나가기</button></div>
      </header>
      <div class="cc-support-staff-messages" id="cc-support-staff-messages" aria-live="polite"><div class="cc-support-staff-empty">상담방을 선택하면 대화가 표시됩니다.</div></div>
      <form class="cc-support-staff-send" id="cc-support-staff-send">
        <input type="file" id="cc-support-staff-file" accept="image/png,image/jpeg,image/gif,image/webp" hidden>
        <textarea name="message" maxlength="1000" rows="1" placeholder="답변 입력" aria-label="상담 답변"></textarea>
        <button type="button" data-admin-action="attach" disabled>사진</button>
        <button type="button" data-admin-action="send" disabled>전송</button>
        <div class="cc-support-admin-attachment" id="cc-support-admin-attachment" hidden><img alt="첨부 이미지 미리보기"><span></span><button type="button" data-admin-action="remove-attachment" aria-label="첨부 취소">×</button></div>
      </form>
    </section>
    <aside class="cc-support-member-preview" aria-label="회원 화면 미리보기">
      <header class="cc-support-preview-head"><strong>회원 화면 미리보기</strong><small>회원에게 보이는 대화 순서입니다.</small></header>
      <div class="cc-support-preview-messages" id="cc-support-preview-messages"><div class="cc-support-staff-empty">회원 화면 미리보기</div></div>
    </aside>
  </div>
</div>
"""


MEMBERS_ADMIN_MARKUP = """
<div class="cc-admin-members">
  <header class="cc-admin-page-head">
    <div><h1>회원 개인정보/등급 관리</h1><p>가입 정보, 등급, 캔디와 이용 상태를 한 화면에서 관리합니다.</p></div>
    <button type="button" class="cc-admin-secondary" id="cc-members-refresh">새로고침</button>
  </header>
  <section class="cc-admin-section cc-member-section">
    <div class="cc-admin-section-head"><div><h2>회원 개인정보/등급 관리</h2><p>비밀번호는 보안상 표시되지 않으며 새 비밀번호 입력 시에만 재설정됩니다.</p></div><label class="cc-member-search"><span>회원 검색</span><input type="search" id="cc-member-search" placeholder="아이디, 닉네임, 이름, 전화번호"></label></div>
    <div class="cc-admin-table-wrap">
      <table class="cc-member-table">
        <thead><tr>
          <th>가입코드</th><th>아이디</th><th>비밀번호</th><th>닉네임</th><th>전화번호</th><th>이름</th><th>권한</th><th>표시등급</th><th>내부등급</th><th>캔디</th><th>잔고(잔고동결)</th><th>계정(계정동결)</th><th>캔디 선물</th><th>IP 밴</th>
        </tr></thead>
        <tbody id="cc-member-rows"><tr><td colspan="14" class="cc-admin-empty">회원 정보를 불러오는 중입니다.</td></tr></tbody>
      </table>
    </div>
  </section>
  <section class="cc-admin-section cc-transaction-section" id="cc-transaction-section">
    <div class="cc-admin-section-head">
      <div><h2>충전/출금 신청</h2><p>회원의 충전과 출금 신청을 한 목록에서 처리합니다. 기존 전용 관리 화면도 그대로 사용할 수 있습니다.</p></div>
      <button type="button" class="cc-admin-secondary" id="cc-transactions-refresh">새로고침</button>
    </div>
    <div class="cc-transaction-toolbar">
      <div class="cc-transaction-pager" id="cc-transaction-pager" aria-label="충전 출금 신청 페이지"></div>
      <span class="cc-transaction-total" id="cc-transaction-total">총 0개</span>
      <div class="cc-transaction-page-size" aria-label="페이지당 표시 개수">
        <button type="button" data-transaction-size="10" class="is-active">10개</button>
        <button type="button" data-transaction-size="100">100개</button>
      </div>
    </div>
    <div class="cc-admin-table-wrap cc-transaction-table-wrap">
      <table class="cc-transaction-table">
        <thead><tr><th>구분</th><th>닉네임</th><th>이름</th><th>금액</th><th>출금계좌</th><th>신청/처리</th><th>상태</th><th>관리</th></tr></thead>
        <tbody id="cc-transaction-rows"><tr><td colspan="8" class="cc-admin-empty">신청 내역을 불러오는 중입니다.</td></tr></tbody>
      </table>
    </div>
  </section>
  <div class="cc-admin-modal" id="cc-transaction-account-modal" hidden>
    <div class="cc-admin-modal-backdrop" data-account-action="close"></div>
    <section class="cc-admin-modal-card cc-account-modal-card" role="dialog" aria-modal="true" aria-labelledby="cc-account-title">
      <header><div><h2 id="cc-account-title">출금계좌 수정</h2><p id="cc-account-member">출금 신청 계좌를 확인하세요.</p></div><button type="button" data-account-action="close" aria-label="닫기">&times;</button></header>
      <form id="cc-transaction-account-form">
        <input type="hidden" name="id">
        <label>은행<input name="bank" maxlength="30" required placeholder="은행명"></label>
        <label>계좌번호<input name="accountNumber" inputmode="numeric" maxlength="30" required placeholder="숫자만 입력"></label>
        <label>예금주<input name="holder" maxlength="40" required placeholder="예금주"></label>
        <div class="cc-admin-modal-actions"><button type="button" class="cc-admin-secondary" data-account-action="close">취소</button><button type="submit" class="cc-admin-primary">저장</button></div>
      </form>
    </section>
  </div>
  <div class="cc-admin-modal" id="cc-gift-modal" hidden>
    <div class="cc-admin-modal-backdrop" data-gift-action="close"></div>
    <section class="cc-admin-modal-card" role="dialog" aria-modal="true" aria-labelledby="cc-gift-title">
      <header><div><h2 id="cc-gift-title">캔디 선물</h2><p id="cc-gift-member">회원을 선택하세요.</p></div><button type="button" data-gift-action="close" aria-label="닫기">&times;</button></header>
      <form id="cc-gift-form">
        <input type="hidden" name="memberId">
        <label>보내는 사람(BJ)<input type="search" id="cc-gift-bj-search" placeholder="BJ 이름 또는 아이디 검색" autocomplete="off"></label>
        <div class="cc-gift-bj-list" id="cc-gift-bj-list" role="listbox"></div>
        <input type="hidden" name="influencerId">
        <label>메시지<textarea name="message" maxlength="1000" rows="4" required placeholder="회원 개인 채팅에 보낼 메시지"></textarea></label>
        <label>캔디 갯수<input name="amount" type="number" min="1" max="9999999999" step="1" required placeholder="0"></label>
        <p class="cc-gift-help">선택한 BJ 이름으로 메시지가 전송되고 회원의 캔디 잔고가 즉시 증가합니다.</p>
        <div class="cc-admin-modal-actions"><button type="button" class="cc-admin-secondary" data-gift-action="close">취소</button><button type="submit" class="cc-admin-primary">선물 보내기</button></div>
      </form>
    </section>
  </div>
  <div class="cc-admin-toast" id="cc-admin-toast" role="status" aria-live="polite" hidden></div>
</div>
"""


PARTNERS_ADMIN_MARKUP = """
<div class="cc-admin-members cc-admin-partners">
  <header class="cc-admin-page-head">
    <div><h1>파트너 추가</h1><p>지정 회원에게 전달할 가입코드를 발급하고 사용 상태를 관리합니다.</p></div>
    <button type="button" class="cc-admin-secondary" id="cc-partners-refresh">새로고침</button>
  </header>
  <section class="cc-admin-section" id="signup-codes">
    <div class="cc-admin-section-head"><div><h2>가입코드 발급</h2><p>영문 대소문자와 숫자 3자 이상으로 발급할 수 있으며, 관리자가 중지하기 전까지 계속 사용할 수 있습니다.</p></div><span id="cc-code-summary">불러오는 중</span></div>
    <form class="cc-code-create" id="cc-code-create">
      <label>가입코드<input name="code" minlength="3" maxlength="32" pattern="[A-Za-z0-9]{3,32}" autocomplete="off" placeholder="예: partner01"></label>
      <label>메모<input name="label" maxlength="60" placeholder="대상자 또는 발급 사유"></label>
      <button type="button" class="cc-admin-secondary" id="cc-code-generate">자동 생성</button>
      <button type="submit" class="cc-admin-primary">코드 발급</button>
    </form>
    <div class="cc-code-list" id="cc-code-list"><p class="cc-admin-empty">가입코드를 불러오는 중입니다.</p></div>
  </section>
  <div class="cc-admin-toast" id="cc-admin-toast" role="status" aria-live="polite" hidden></div>
</div>
"""


MEMBER_CHAT_ADMIN_MARKUP = """
<div class="cc-admin-member-chat">
  <header class="cc-admin-page-head">
    <div><h1>개인 채팅</h1><p>회원을 선택하고 원하는 BJ 이름으로 개인 메시지를 보냅니다.</p></div>
    <button type="button" class="cc-admin-secondary" id="cc-chat-refresh">새로고침</button>
  </header>
  <div class="cc-chat-admin-grid">
    <aside class="cc-chat-room-panel">
      <label class="cc-chat-search"><span>대화 검색</span><input type="search" id="cc-chat-room-search" placeholder="회원, BJ, 메시지"></label>
      <div class="cc-chat-room-list" id="cc-chat-room-list"><p class="cc-admin-empty">최근 대화를 불러오는 중입니다.</p></div>
    </aside>
    <section class="cc-chat-conversation">
      <header class="cc-chat-conversation-head">
        <div class="cc-chat-party-field"><label for="cc-chat-member-search">받는 회원</label><input type="search" id="cc-chat-member-search" placeholder="회원 검색"><select id="cc-chat-member-select" aria-label="받는 회원"><option value="">회원을 선택하세요</option></select></div>
        <div class="cc-chat-party-field"><label for="cc-chat-bj-search">보내는 BJ</label><input type="search" id="cc-chat-bj-search" placeholder="BJ 검색"><select id="cc-chat-bj-select" aria-label="보내는 BJ"><option value="">BJ를 선택하세요</option></select></div>
        <button type="button" class="cc-admin-primary" id="cc-chat-open">대화 열기</button>
      </header>
      <div class="cc-chat-active-meta" id="cc-chat-active-meta"><strong>대화 상대를 선택하세요.</strong><span>회원과 BJ를 선택하면 기존 대화가 표시됩니다.</span></div>
      <div class="cc-chat-admin-messages" id="cc-chat-admin-messages" aria-live="polite"><p class="cc-admin-empty">표시할 대화가 없습니다.</p></div>
      <form class="cc-chat-admin-composer" id="cc-chat-admin-composer">
        <div class="cc-chat-admin-attachment" id="cc-chat-admin-attachment" hidden><img alt="첨부 이미지 미리보기"><span></span><button type="button" data-chat-action="remove-attachment" aria-label="첨부 이미지 삭제">&times;</button></div>
        <div class="cc-chat-admin-composer-row">
          <input id="cc-chat-admin-file" type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden>
          <button type="button" class="cc-chat-admin-attach" data-chat-action="attach" aria-label="사진 첨부" disabled>&#128206;</button>
          <textarea name="message" rows="2" maxlength="1000" placeholder="선택한 BJ 이름으로 보낼 메시지" disabled></textarea>
          <button type="submit" class="cc-admin-primary" disabled>전송</button>
        </div>
      </form>
    </section>
  </div>
  <div class="cc-admin-toast" id="cc-admin-toast" role="status" aria-live="polite" hidden></div>
</div>
"""


def normalize_support_attachment(value: object) -> tuple[str, str, str]:
    if not value:
        return "", "", ""
    if not isinstance(value, dict):
        raise ValueError("첨부 이미지 형식이 올바르지 않습니다.")
    name = Path(str(value.get("name", "image"))).name[:120]
    supplied_type = str(value.get("type", "")).lower().strip()
    data = str(value.get("data", "")).strip()
    match = re.fullmatch(
        r"data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=]+)",
        data,
    )
    if not match:
        raise ValueError("PNG, JPG, GIF, WEBP 이미지만 첨부할 수 있습니다.")
    attachment_type = match.group(1)
    if supplied_type and supplied_type != attachment_type:
        raise ValueError("첨부 이미지 형식이 일치하지 않습니다.")
    try:
        decoded = base64.b64decode(match.group(2), validate=True)
    except Exception as exc:
        raise ValueError("첨부 이미지를 읽을 수 없습니다.") from exc
    if not decoded or len(decoded) > SUPPORT_MAX_ATTACHMENT_BYTES:
        raise ValueError("첨부 이미지는 2MB 이하만 사용할 수 있습니다.")
    return name or "image", attachment_type, data


def support_message_payload(row: sqlite3.Row) -> dict[str, object]:
    deleted_by_member = bool(row["deleted_by_member"])
    attachment = None
    if row["attachment_data"] and not deleted_by_member:
        attachment = {
            "name": row["attachment_name"],
            "type": row["attachment_type"],
            "data": row["attachment_data"],
        }
    return {
        "id": row["id"],
        "senderType": row["sender_type"],
        "senderId": row["sender_id"],
        "message": "" if deleted_by_member else row["message"],
        "attachment": attachment,
        "createdAt": row["created_at"],
        "editedAt": row["edited_at"],
        "deletedByMember": deleted_by_member,
    }


def member_chat_message_payload(row: sqlite3.Row, member_id: str) -> dict[str, object]:
    deleted_by_member = bool(row["deleted_by_member"])
    attachment = None
    if row["attachment_data"] and not deleted_by_member:
        attachment = {
            "name": row["attachment_name"],
            "type": row["attachment_type"],
            "data": row["attachment_data"],
        }
    return {
        "id": int(row["id"]),
        "sender": "member" if row["sender_id"] == member_id else "influencer",
        "message": "" if deleted_by_member else row["message"],
        "attachment": attachment,
        "createdAt": row["created_at"],
        "readAt": row["read_at"],
        "editedAt": row["edited_at"],
        "deletedByMember": deleted_by_member,
    }


def live_id_by_archive(root: Path) -> dict[str, str]:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return {}
    index_path = root / "index.html"
    if not index_path.is_file():
        return {}
    soup = BeautifulSoup(read_text(index_path), "html.parser")
    result: dict[str, str] = {}
    for link in soup.select('a[href^="/live.php?live_id="]'):
        parsed = urlparse(link.get("href", ""))
        live_id = parse_qs(parsed.query).get("live_id", [""])[0]
        if live_id:
            result[query_hash(f"live_id={live_id}")] = live_id
    return result


_MEMBER_CHAT_PROFILE_CACHE: dict[str, object] = {}


def member_chat_profiles(chatlist_path: Path) -> dict[str, dict[str, str]]:
    if not chatlist_path.is_file():
        return {}
    modified = chatlist_path.stat().st_mtime_ns
    cache_path = str(chatlist_path.resolve())
    if (
        _MEMBER_CHAT_PROFILE_CACHE.get("path") == cache_path
        and _MEMBER_CHAT_PROFILE_CACHE.get("modified") == modified
    ):
        return _MEMBER_CHAT_PROFILE_CACHE.get("profiles", {})  # type: ignore[return-value]
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return {}

    soup = BeautifulSoup(read_text(chatlist_path), "html.parser")
    profiles: dict[str, dict[str, str]] = {}
    for card in soup.select(".maindddd > .mvdfk"):
        entry = card.select_one(".woiej[onclick]")
        if entry is None:
            continue
        onclick = entry.get("onclick", "")
        match = re.search(r"openWindow\([^,]*,\s*['\"]([^'\"]+)['\"]\)", onclick)
        if match is None:
            match = re.search(r"me_recv_mb_id=([^'\"&]+)", onclick)
        if match is None:
            continue
        influencer_id = unquote(match.group(1)).strip()
        if not influencer_id:
            continue

        title_node = card.select_one(".mjyehn")
        nickname_node = card.select_one(".lpkojhg")
        if nickname_node is not None:
            participant_count = nickname_node.select_one(".llooiik")
            if participant_count is not None:
                participant_count.decompose()
        image_node = card.select_one(".zxcmnv img")
        room_title = title_node.get_text(" ", strip=True) if title_node else ""
        nickname = nickname_node.get_text(" ", strip=True) if nickname_node else ""
        image_src = image_node.get("src", "") if image_node else ""
        if not image_src.startswith("/") or image_src.startswith("//"):
            image_src = "/img/no_profile.gif"
        profiles[influencer_id] = {
            "id": influencer_id,
            "name": room_title or nickname or influencer_id,
            "nickname": nickname or room_title or influencer_id,
            "image": image_src,
        }

    _MEMBER_CHAT_PROFILE_CACHE.clear()
    _MEMBER_CHAT_PROFILE_CACHE.update(
        {"path": cache_path, "modified": modified, "profiles": profiles}
    )
    return profiles


class StandaloneHandler(BaseHTTPRequestHandler):
    root: Path
    db_path: Path

    def send_bytes(
        self,
        data: bytes,
        content_type: str = "text/html; charset=utf-8",
        status: int = 200,
        cookies: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def send_json(self, payload: object, status: int = 200) -> None:
        self.send_bytes(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
            headers={"Cache-Control": "no-store"},
        )

    def send_redirect(self, location: str, cookies: list[str] | None = None) -> None:
        self.send_response(HTTPStatus.FOUND)
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Location", location)
        self.end_headers()

    def cookie_value(self, name: str) -> str:
        raw_cookie = self.headers.get("Cookie", "")
        if not raw_cookie:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            return ""
        morsel = cookie.get(name)
        return morsel.value if morsel is not None else ""

    def session_token(self) -> str:
        return self.cookie_value(SESSION_COOKIE)

    def request_ip(self) -> str:
        return normalize_ip(
            self.headers.get("CF-Connecting-IP")
            or self.headers.get("X-Forwarded-For")
            or (self.client_address[0] if self.client_address else "")
        )

    def remember_member_ip(self, username: str, force: bool = False) -> None:
        if not username or username == ADMIN_ID:
            return
        address = self.request_ip()
        if not address:
            return
        now_monotonic = time.monotonic()
        previous = MEMBER_IP_TOUCHES.get(username)
        if not force and previous and previous[0] == address and now_monotonic - previous[1] < 30:
            return
        try:
            with sqlite3.connect(self.db_path, timeout=5) as db:
                db.execute(
                    "UPDATE users SET last_ip=?,last_ip_at=? WHERE id=?",
                    (address, now_text(), username),
                )
                db.commit()
        except sqlite3.Error:
            return
        MEMBER_IP_TOUCHES[username] = (address, now_monotonic)

    def block_banned_request(
        self,
        path: str,
        data: dict[str, object] | None = None,
    ) -> bool:
        address = self.request_ip()
        if not address or address not in BANNED_IPS:
            return False
        token = self.session_token()
        session_user = ACTIVE_SESSIONS.get(token, "") if token else ""
        if session_user == ADMIN_ID:
            return False
        if path == "/bbs/login.php" or path.startswith(
            ("/assets/", "/img/", "/ftv/", "/css/", "/js/", "/adm/css/", "/adm/js/")
        ):
            return False
        if path == "/bbs/login_check.php" and str((data or {}).get("mb_id", "")).strip() == ADMIN_ID:
            return False
        if token:
            ACTIVE_SESSIONS.pop(token, None)
            ACTIVE_SESSION_SEEN.pop(token, None)
        if path.startswith("/api/"):
            self.send_json({"error": IP_BAN_MESSAGE}, HTTPStatus.FORBIDDEN)
        else:
            page = html_page(
                "접속 제한",
                "<main style='min-height:100vh;display:grid;place-items:center;background:#f7f8fb'>"
                "<section style='padding:34px 40px;border:1px solid #dfe3ea;background:#fff;text-align:center'>"
                "<h1 style='margin:0 0 10px'>접속이 제한되었습니다.</h1>"
                "<p style='margin:0;color:#697181'>관리자에게 문의해 주세요.</p></section></main>",
            )
            self.send_bytes(page, status=HTTPStatus.FORBIDDEN)
        return True

    def current_user(self) -> str:
        token = self.session_token()
        if not token:
            return ""
        username = ACTIVE_SESSIONS.get(token, "")
        if username:
            ACTIVE_SESSION_SEEN[token] = time.monotonic()
            self.remember_member_ip(username)
        return username

    def display_name(self, username: str) -> str:
        if username == ADMIN_ID:
            return "상담원"
        if not username:
            return ""
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT nickname FROM users WHERE id=?",
                (username,),
            ).fetchone()
        return row[0] if row and row[0] else username

    def balance(self, username: str) -> int:
        if not username:
            return 0
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT balance FROM wallets WHERE member_id=?",
                (username,),
            ).fetchone()
        return int(row[0]) if row else 0

    def member_state(self, username: str) -> dict[str, object]:
        state: dict[str, object] = {
            "role": "MEMBER",
            "display_grade": DISPLAY_GRADES[0],
            "internal_grade": 1,
            "balance_status": BALANCE_STATUSES[0],
            "account_status": ACCOUNT_STATUSES[0],
            "profile_image_url": PROFILE_FALLBACK_IMAGE,
        }
        if not username or username == ADMIN_ID:
            return state
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                """SELECT role,display_grade,internal_grade,balance_status,account_status,
                          LENGTH(profile_image),profile_image_updated_at
                   FROM users WHERE id=?""",
                (username,),
            ).fetchone()
        if row is None:
            return state
        state.update(
            {
                "role": row[0] if row[0] in MEMBER_ROLES else "MEMBER",
                "display_grade": row[1] if row[1] in DISPLAY_GRADES else DISPLAY_GRADES[0],
                "internal_grade": max(1, min(10, int(row[2] or 1))),
                "balance_status": row[3] if row[3] in BALANCE_STATUSES else BALANCE_STATUSES[0],
                "account_status": row[4] if row[4] in ACCOUNT_STATUSES else ACCOUNT_STATUSES[0],
                "profile_image_url": (
                    f"{PROFILE_MEDIA_PATH}?v={quote(str(row[6] or '1'), safe='')}"
                    if int(row[5] or 0) > 0
                    else PROFILE_FALLBACK_IMAGE
                ),
            }
        )
        return state

    def send_member_profile_image(self, username: str) -> None:
        if not username or username == ADMIN_ID:
            self.send_json({"error": "로그인이 필요합니다."}, HTTPStatus.UNAUTHORIZED)
            return
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT profile_image,profile_image_updated_at FROM users WHERE id=?",
                (username,),
            ).fetchone()
        if row is None:
            self.send_json({"error": "회원 정보를 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        image_data = bytes(row[0] or b"")
        if not image_data:
            fallback = self.root / PROFILE_FALLBACK_IMAGE.lstrip("/")
            if fallback.is_file():
                self.send_bytes(
                    fallback.read_bytes(),
                    "image/gif",
                    headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"},
                )
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        etag = hashlib.sha256(image_data).hexdigest()[:24]
        self.send_bytes(
            image_data,
            "image/webp",
            headers={
                "Cache-Control": "private, max-age=31536000, immutable",
                "ETag": f'"{etag}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    def save_member_profile(self, username: str, data: dict[str, object]) -> dict[str, object]:
        image_data = normalize_profile_image(data.get("image"))
        updated_at = datetime.now().isoformat(timespec="microseconds")
        with sqlite3.connect(self.db_path, timeout=10) as db:
            cursor = db.execute(
                """UPDATE users
                   SET profile_image=?,profile_image_mime='image/webp',profile_image_updated_at=?
                   WHERE id=?""",
                (image_data, updated_at, username),
            )
            if cursor.rowcount != 1:
                raise LookupError("회원 정보를 찾을 수 없습니다.")
            db.commit()
        return {
            "ok": True,
            "message": "프로필 이미지가 저장되었습니다.",
            "profileUrl": f"{PROFILE_MEDIA_PATH}?v={quote(updated_at, safe='')}",
        }

    def delete_member_profile(self, username: str) -> dict[str, object]:
        updated_at = datetime.now().isoformat(timespec="microseconds")
        with sqlite3.connect(self.db_path, timeout=10) as db:
            cursor = db.execute(
                """UPDATE users
                   SET profile_image=X'',profile_image_mime='',profile_image_updated_at=?
                   WHERE id=?""",
                (updated_at, username),
            )
            if cursor.rowcount != 1:
                raise LookupError("회원 정보를 찾을 수 없습니다.")
            db.commit()
        return {
            "ok": True,
            "message": "프로필 이미지가 삭제되었습니다.",
            "profileUrl": PROFILE_FALLBACK_IMAGE,
        }

    def member_action_restriction(
        self,
        username: str,
        require_balance: bool = False,
    ) -> str:
        state = self.member_state(username)
        if state["account_status"] == "계정동결":
            return ACCOUNT_RESTRICTION_MESSAGE
        if require_balance and state["balance_status"] == "잔고동결":
            return BALANCE_RESTRICTION_MESSAGE
        return ""

    def admin_members_payload(self, search: str = "") -> dict[str, object]:
        term = search.strip()[:80]
        params: list[object] = []
        where = ""
        if term:
            like = f"%{term}%"
            where = """WHERE u.id LIKE ? OR u.nickname LIKE ? OR u.name LIKE ?
                       OR u.phone LIKE ? OR u.signup_code LIKE ?"""
            params.extend([like, like, like, like, like])
        with self.support_db() as db:
            rows = db.execute(
                f"""SELECT u.id,u.signup_code,u.nickname,u.phone,u.name,u.role,
                           u.display_grade,u.internal_grade,u.balance_status,u.account_status,
                           u.created_at,u.last_ip,u.last_ip_at,
                           CASE WHEN b.ip IS NULL THEN 0 ELSE 1 END AS ip_banned,
                           COALESCE(w.balance,u.balance,0) AS candy
                    FROM users u LEFT JOIN wallets w ON w.member_id=u.id
                    LEFT JOIN ip_bans b ON b.ip=u.last_ip
                    {where}
                    ORDER BY u.created_at DESC,u.id ASC LIMIT 1000""",
                params,
            ).fetchall()
        now_monotonic = time.monotonic()
        online_members = {
            ACTIVE_SESSIONS.get(token, "")
            for token, last_seen in ACTIVE_SESSION_SEEN.items()
            if now_monotonic - last_seen < 120
        }
        members = [
            {
                "id": row["id"],
                "signupCode": row["signup_code"],
                "nickname": row["nickname"],
                "phone": row["phone"],
                "name": row["name"],
                "role": row["role"] if row["role"] in MEMBER_ROLES else "MEMBER",
                "displayGrade": row["display_grade"] if row["display_grade"] in DISPLAY_GRADES else DISPLAY_GRADES[0],
                "internalGrade": max(1, min(10, int(row["internal_grade"] or 1))),
                "candy": int(row["candy"] or 0),
                "balanceStatus": row["balance_status"] if row["balance_status"] in BALANCE_STATUSES else BALANCE_STATUSES[0],
                "accountStatus": row["account_status"] if row["account_status"] in ACCOUNT_STATUSES else ACCOUNT_STATUSES[0],
                "lastIp": normalize_ip(row["last_ip"]),
                "lastIpAt": row["last_ip_at"] or "",
                "ipBanned": bool(row["ip_banned"]),
                "createdAt": row["created_at"],
                "online": row["id"] in online_members,
            }
            for row in rows
        ]
        return {
            "members": members,
            "choices": {
                "roles": [{"value": value, "label": label} for value, label in MEMBER_ROLES.items()],
                "displayGrades": list(DISPLAY_GRADES),
                "internalGrades": list(range(1, 11)),
                "balanceStatuses": list(BALANCE_STATUSES),
                "accountStatuses": list(ACCOUNT_STATUSES),
            },
        }

    def admin_transactions_payload(self, query: dict[str, list[str]]) -> dict[str, object]:
        try:
            page = max(1, int(query.get("page", ["1"])[0]))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = int(query.get("per_page", ["10"])[0])
        except (TypeError, ValueError):
            per_page = 10
        per_page = 100 if per_page == 100 else 10
        offset = (page - 1) * per_page
        with self.support_db() as db:
            total = int(
                db.execute(
                    "SELECT COUNT(*) FROM transactions WHERE COALESCE(is_deleted,0)=0"
                ).fetchone()[0]
            )
            total_pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, total_pages)
            offset = (page - 1) * per_page
            rows = db.execute(
                """SELECT t.id,t.kind,t.member_id,t.name,t.bank,t.bankno,t.price,t.count,
                          t.status,t.created_at,t.handled_at,t.handled_by,
                          u.nickname,u.name AS member_name
                   FROM transactions t LEFT JOIN users u ON u.id=t.member_id
                   WHERE COALESCE(t.is_deleted,0)=0
                   ORDER BY t.id DESC LIMIT ? OFFSET ?""",
                (per_page, offset),
            ).fetchall()

        transactions = []
        for row in rows:
            is_withdraw = row["kind"] == "export"
            raw_status = str(row["status"] or "대기")
            completed = raw_status in {"완료", "승인"}
            transactions.append(
                {
                    "id": int(row["id"]),
                    "type": "withdraw" if is_withdraw else "charge",
                    "typeLabel": "출금" if is_withdraw else "충전",
                    "memberId": row["member_id"],
                    "nickname": row["nickname"] or row["member_id"],
                    "name": row["member_name"] or row["name"] or "-",
                    "amount": int(row["price"] or 0),
                    "candy": int(row["count"] or row["price"] or 0),
                    "account": (
                        {
                            "bank": row["bank"] or "",
                            "number": row["bankno"] or "",
                            "holder": row["name"] or row["member_name"] or "",
                        }
                        if is_withdraw
                        else None
                    ),
                    "status": "완료" if completed else raw_status,
                    "rawStatus": raw_status,
                    "pending": raw_status == "대기",
                    "completed": completed,
                    "createdAt": row["created_at"],
                    "handledAt": row["handled_at"] or "",
                    "handledBy": row["handled_by"] or "",
                }
            )
        return {
            "transactions": transactions,
            "page": page,
            "perPage": per_page,
            "total": total,
            "totalPages": total_pages,
        }

    @staticmethod
    def transition_transaction(
        db: sqlite3.Connection,
        row: sqlite3.Row,
        target_status: str,
    ) -> None:
        previous_status = str(row["status"] or "대기")
        if previous_status == target_status:
            return
        member_id = str(row["member_id"])
        kind = str(row["kind"])
        price = int(row["price"] or 0)
        candy = int(row["count"] or price)
        wallet = db.execute(
            "SELECT balance FROM wallets WHERE member_id=?",
            (member_id,),
        ).fetchone()
        current_balance = int(wallet["balance"] or 0) if wallet is not None else 0
        new_balance = current_balance

        if kind == "export":
            refunded_states = {"취소", "동결"}
            was_refunded = previous_status in refunded_states
            will_be_refunded = target_status in refunded_states
            if will_be_refunded and not was_refunded:
                new_balance += price
            elif was_refunded and not will_be_refunded:
                if current_balance < price:
                    raise ValueError("회원 캔디 잔액이 부족해 출금 상태를 변경할 수 없습니다.")
                new_balance -= price
        elif kind == "import":
            credited_states = {"완료", "승인"}
            was_credited = previous_status in credited_states
            will_be_credited = target_status in credited_states
            if will_be_credited and not was_credited:
                if current_balance + candy > MAX_CANDY_BALANCE:
                    raise ValueError("충전 후 캔디 잔액이 허용 범위를 초과합니다.")
                new_balance += candy
            elif was_credited and not will_be_credited:
                if current_balance < candy:
                    raise ValueError("회원 캔디 잔액이 부족해 완료 처리를 롤백할 수 없습니다.")
                new_balance -= candy

        if new_balance != current_balance:
            db.execute(
                """INSERT INTO wallets(member_id,balance) VALUES(?,?)
                   ON CONFLICT(member_id) DO UPDATE SET balance=excluded.balance""",
                (member_id, new_balance),
            )
            db.execute("UPDATE users SET balance=? WHERE id=?", (new_balance, member_id))
        handled_at = "" if target_status == "대기" else now_text()
        db.execute(
            """UPDATE transactions
               SET status=?,handled_at=?,handled_by=? WHERE id=?""",
            (target_status, handled_at, ADMIN_ID, int(row["id"])),
        )

    def set_transaction_status(self, transaction_id: int, kind: str, status: str) -> dict[str, object]:
        if kind not in {"import", "export"}:
            raise ValueError("신청 종류가 올바르지 않습니다.")
        if status not in {"대기", "동결", "취소", "승인", "완료"}:
            raise ValueError("변경할 상태가 올바르지 않습니다.")
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id,kind,member_id,price,count,status
                   FROM transactions
                   WHERE id=? AND kind=? AND COALESCE(is_deleted,0)=0""",
                (transaction_id, kind),
            ).fetchone()
            if row is None:
                raise LookupError("신청 내역을 찾을 수 없습니다.")
            self.transition_transaction(db, row, status)
            db.commit()
        return {"ok": True, "id": transaction_id, "status": status}

    def admin_transaction_action(self, data: dict[str, object]) -> dict[str, object]:
        try:
            transaction_id = int(data.get("id", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("신청 번호가 올바르지 않습니다.") from exc
        action = str(data.get("action", "")).strip()
        if transaction_id <= 0 or action not in {"complete", "cancel", "rollback", "delete"}:
            raise ValueError("처리할 신청과 작업을 확인해주세요.")
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id,kind,member_id,price,count,status
                   FROM transactions WHERE id=? AND COALESCE(is_deleted,0)=0""",
                (transaction_id,),
            ).fetchone()
            if row is None:
                raise LookupError("신청 내역을 찾을 수 없습니다.")
            previous_status = str(row["status"] or "대기")
            completed_states = {"완료", "승인"}
            if action == "complete":
                if previous_status != "대기":
                    raise ValueError("대기 상태의 신청만 완료 처리할 수 있습니다.")
                target_status = "승인" if row["kind"] == "export" else "완료"
                self.transition_transaction(db, row, target_status)
            elif action == "cancel":
                if previous_status != "대기":
                    raise ValueError("대기 상태의 신청만 취소할 수 있습니다.")
                target_status = "취소"
                self.transition_transaction(db, row, target_status)
            elif action == "rollback":
                if previous_status not in completed_states:
                    raise ValueError("완료된 신청만 롤백할 수 있습니다.")
                target_status = "대기"
                self.transition_transaction(db, row, target_status)
            else:
                if previous_status == "대기":
                    self.transition_transaction(db, row, "취소")
                db.execute(
                    """UPDATE transactions SET is_deleted=1,handled_at=?,handled_by=?
                       WHERE id=?""",
                    (now_text(), ADMIN_ID, transaction_id),
                )
                target_status = "삭제"
            db.commit()
        return {"ok": True, "id": transaction_id, "status": target_status}

    def update_transaction_account(self, data: dict[str, object]) -> dict[str, object]:
        try:
            transaction_id = int(data.get("id", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("신청 번호가 올바르지 않습니다.") from exc
        bank = str(data.get("bank", "")).strip()[:30]
        account_number = re.sub(r"[^0-9]", "", str(data.get("accountNumber", "")))[:30]
        holder = str(data.get("holder", "")).strip()[:40]
        if transaction_id <= 0 or not bank or not 5 <= len(account_number) <= 30 or not holder:
            raise ValueError("은행, 계좌번호, 예금주를 정확히 입력해주세요.")
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id FROM transactions
                   WHERE id=? AND kind='export' AND COALESCE(is_deleted,0)=0""",
                (transaction_id,),
            ).fetchone()
            if row is None:
                raise LookupError("수정할 출금 신청을 찾을 수 없습니다.")
            db.execute(
                "UPDATE transactions SET bank=?,bankno=?,name=? WHERE id=?",
                (bank, account_number, holder, transaction_id),
            )
            db.commit()
        return {"ok": True, "id": transaction_id}

    def admin_signup_codes_payload(self) -> dict[str, object]:
        with self.support_db() as db:
            rows = db.execute(
                """SELECT c.code,c.label,c.active,c.created_at,
                          COUNT(u.id) AS use_count,COALESCE(MAX(u.created_at),'') AS last_used_at
                   FROM signup_codes c LEFT JOIN users u ON u.signup_code=c.code
                   GROUP BY c.code,c.label,c.active,c.created_at
                   ORDER BY c.created_at DESC,c.code ASC LIMIT 1000"""
            ).fetchall()
        codes = [
            {
                "code": row["code"],
                "label": row["label"],
                "active": bool(row["active"]),
                "useCount": int(row["use_count"] or 0),
                "lastUsedAt": row["last_used_at"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]
        return {
            "codes": codes,
            "available": sum(1 for item in codes if item["active"]),
        }

    def admin_influencers_payload(self, search: str = "") -> dict[str, object]:
        term = search.strip().casefold()[:80]
        profiles = member_chat_profiles(self.root / "chatlist.php.html")
        influencers = []
        for profile in profiles.values():
            haystack = " ".join(
                [profile.get("id", ""), profile.get("name", ""), profile.get("nickname", "")]
            ).casefold()
            if term and term not in haystack:
                continue
            influencers.append(dict(profile))
        influencers.sort(key=lambda item: (item.get("name", "").casefold(), item.get("id", "")))
        return {"influencers": influencers[:1000]}

    def admin_member_chat_rooms_payload(self, search: str = "") -> dict[str, object]:
        term = search.strip()[:80]
        params: list[object] = []
        where = ""
        if term:
            like = f"%{term}%"
            where = """AND (r.member_id LIKE ? OR u.nickname LIKE ? OR u.name LIKE ?
                              OR r.influencer_id LIKE ? OR COALESCE((
                                  SELECT CASE WHEN m.message<>'' THEN m.message ELSE '[이미지]' END
                                  FROM chat_messages m
                                   WHERE m.member_id=r.member_id
                                     AND m.influencer_id=r.influencer_id
                                     AND m.deleted_by_member=0
                                   ORDER BY m.id DESC LIMIT 1
                              ),'') LIKE ?)"""
            params.extend([like, like, like, like, like])
        with self.support_db() as db:
            rows = db.execute(
                f"""SELECT r.member_id,r.influencer_id,r.last_at,u.nickname,u.name,
                           COALESCE((SELECT CASE WHEN m.message<>'' THEN m.message ELSE '[이미지]' END
                             FROM chat_messages m
                             WHERE m.member_id=r.member_id
                               AND m.influencer_id=r.influencer_id
                               AND m.deleted_by_member=0
                             ORDER BY m.id DESC LIMIT 1),'') AS last_message,
                           (SELECT COUNT(*) FROM chat_messages m
                             WHERE m.member_id=r.member_id
                                AND m.influencer_id=r.influencer_id
                                AND m.sender_id=r.member_id
                                AND m.deleted_by_member=0
                                AND m.read_at='') AS unread
                    FROM member_chat_rooms r JOIN users u ON u.id=r.member_id
                    WHERE r.influencer_id NOT LIKE 'live:%' {where}
                    ORDER BY r.last_at DESC,r.member_id ASC LIMIT 500""",
                params,
            ).fetchall()
        rooms = []
        for row in rows:
            profile = self.member_chat_profile(str(row["influencer_id"]))
            rooms.append(
                {
                    "memberId": row["member_id"],
                    "memberName": row["nickname"] or row["name"] or row["member_id"],
                    "influencer": profile,
                    "lastMessage": row["last_message"] or "대화를 시작해보세요.",
                    "lastAt": row["last_at"],
                    "unread": int(row["unread"] or 0),
                }
            )
        return {"rooms": rooms}

    def admin_member_chat_payload(
        self,
        member_id: str,
        influencer_id: str,
        mark_read: bool = True,
    ) -> dict[str, object] | None:
        profiles = member_chat_profiles(self.root / "chatlist.php.html")
        profile = profiles.get(influencer_id)
        if profile is None:
            return None
        with self.support_db() as db:
            member = db.execute(
                "SELECT id,nickname,name,phone FROM users WHERE id=?",
                (member_id,),
            ).fetchone()
            if member is None:
                return None
            if mark_read:
                db.execute(
                    """UPDATE chat_messages SET read_at=?
                       WHERE member_id=? AND influencer_id=?
                         AND sender_id=? AND receiver_id=? AND read_at=''""",
                    (now_text(), member_id, influencer_id, member_id, influencer_id),
                )
            rows = db.execute(
                """SELECT id,sender_id,receiver_id,message,
                          attachment_name,attachment_type,attachment_data,
                          created_at,read_at,edited_at,deleted_by_member
                   FROM chat_messages
                   WHERE member_id=? AND influencer_id=?
                   ORDER BY id ASC LIMIT 1000""",
                (member_id, influencer_id),
            ).fetchall()
            db.commit()
        return {
            "member": {
                "id": member["id"],
                "nickname": member["nickname"],
                "name": member["name"],
                "phone": member["phone"],
            },
            "influencer": dict(profile),
            "messages": [member_chat_message_payload(row, member_id) for row in rows],
        }

    def update_member_from_admin(self, data: dict[str, object]) -> dict[str, object]:
        original_id = str(data.get("originalId", "")).strip()
        member_id = str(data.get("id", "")).strip()
        nickname = str(data.get("nickname", "")).strip()
        phone = re.sub(r"[^0-9]", "", str(data.get("phone", "")))
        name = str(data.get("name", "")).strip()
        role = str(data.get("role", "MEMBER"))
        display_grade = str(data.get("displayGrade", DISPLAY_GRADES[0]))
        balance_status = str(data.get("balanceStatus", BALANCE_STATUSES[0]))
        account_status = str(data.get("accountStatus", ACCOUNT_STATUSES[0]))
        password = str(data.get("password", ""))
        try:
            internal_grade = int(data.get("internalGrade", 1))
            candy = int(str(data.get("candy", 0)).replace(",", ""))
        except (TypeError, ValueError) as exc:
            raise ValueError("내부등급과 캔디는 숫자로 입력해주세요.") from exc

        if not original_id:
            raise ValueError("수정할 회원을 찾을 수 없습니다.")
        if not re.fullmatch(r"[A-Za-z0-9]{6,15}", member_id) or member_id.lower() == ADMIN_ID:
            raise ValueError("아이디는 6~15자의 영문과 숫자로 입력해주세요.")
        if not 2 <= len(nickname) <= 20:
            raise ValueError("닉네임은 2~20자로 입력해주세요.")
        if not name:
            raise ValueError("이름을 입력해주세요.")
        if len(phone) > 20:
            raise ValueError("전화번호를 확인해주세요.")
        if role not in MEMBER_ROLES:
            raise ValueError("권한이 올바르지 않습니다.")
        if display_grade not in DISPLAY_GRADES:
            raise ValueError("표시등급이 올바르지 않습니다.")
        if not 1 <= internal_grade <= 10:
            raise ValueError("내부등급은 1~10등급으로 선택해주세요.")
        if not 0 <= candy <= MAX_CANDY_BALANCE:
            raise ValueError("캔디는 0부터 9,999,999,999까지 입력할 수 있습니다.")
        if balance_status not in BALANCE_STATUSES:
            raise ValueError("잔고 상태가 올바르지 않습니다.")
        if account_status not in ACCOUNT_STATUSES:
            raise ValueError("계정 상태가 올바르지 않습니다.")
        if password and not (
            8 <= len(password) <= 15
            and re.search(r"[A-Za-z]", password)
            and re.search(r"[0-9]", password)
            and re.search(r"[^A-Za-z0-9]", password)
        ):
            raise ValueError("새 비밀번호는 8~15자의 영문, 숫자, 특수문자를 포함해야 합니다.")

        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT 1 FROM users WHERE id=?", (original_id,)).fetchone()
            if existing is None:
                raise LookupError("회원을 찾을 수 없습니다.")
            if member_id != original_id:
                duplicate = db.execute("SELECT 1 FROM users WHERE id=?", (member_id,)).fetchone()
                if duplicate is not None:
                    raise ValueError("이미 사용 중인 아이디입니다.")
            password_sql = ""
            password_params: list[object] = []
            if password:
                salt, digest = hash_password(password)
                password_sql = ",password_salt=?,password_hash=?"
                password_params.extend([salt, digest])
            db.execute(
                f"""UPDATE users SET id=?,nickname=?,phone=?,name=?,role=?,display_grade=?,
                       internal_grade=?,balance=?,balance_status=?,account_status=?{password_sql}
                    WHERE id=?""",
                [
                    member_id,
                    nickname,
                    phone,
                    name,
                    role,
                    display_grade,
                    internal_grade,
                    candy,
                    balance_status,
                    account_status,
                    *password_params,
                    original_id,
                ],
            )
            if member_id != original_id:
                for statement in (
                    "UPDATE wallets SET member_id=? WHERE member_id=?",
                    "UPDATE transactions SET member_id=? WHERE member_id=?",
                    "UPDATE support_rooms SET member_id=? WHERE member_id=?",
                    "UPDATE signup_codes SET used_by=? WHERE used_by=?",
                    "UPDATE candy_gifts SET member_id=? WHERE member_id=?",
                    "UPDATE member_chat_rooms SET member_id=? WHERE member_id=?",
                    "UPDATE chat_messages SET member_id=? WHERE member_id=?",
                    "UPDATE chat_messages SET sender_id=? WHERE sender_id=?",
                    "UPDATE chat_messages SET receiver_id=? WHERE receiver_id=?",
                    "UPDATE ip_bans SET member_id=? WHERE member_id=?",
                ):
                    db.execute(statement, (member_id, original_id))
            db.execute(
                """INSERT INTO wallets(member_id,balance) VALUES(?,?)
                   ON CONFLICT(member_id) DO UPDATE SET balance=excluded.balance""",
                (member_id, candy),
            )
            db.commit()
        if member_id != original_id:
            for token, active_user in list(ACTIVE_SESSIONS.items()):
                if active_user == original_id:
                    ACTIVE_SESSIONS[token] = member_id
            if original_id in MEMBER_IP_TOUCHES:
                MEMBER_IP_TOUCHES[member_id] = MEMBER_IP_TOUCHES.pop(original_id)
        return {"ok": True, "id": member_id}

    def set_member_ip_ban(self, data: dict[str, object]) -> dict[str, object]:
        member_id = str(data.get("memberId", "")).strip()[:100]
        raw_banned = data.get("banned")
        banned = raw_banned is True or str(raw_banned).lower() in {"1", "true", "on"}
        with self.support_db() as db:
            row = db.execute(
                "SELECT nickname,last_ip FROM users WHERE id=?",
                (member_id,),
            ).fetchone()
            if row is None:
                raise LookupError("회원을 찾을 수 없습니다.")
            address = normalize_ip(row["last_ip"])
            if not address:
                raise ValueError("기록된 회원 IP가 없습니다.")
            if banned:
                db.execute(
                    """INSERT INTO ip_bans(ip,member_id,memo,created_at,created_by)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(ip) DO UPDATE SET member_id=excluded.member_id,
                           memo=excluded.memo,created_at=excluded.created_at,
                           created_by=excluded.created_by""",
                    (address, member_id, f"{row['nickname'] or member_id} 회원 IP", now_text(), ADMIN_ID),
                )
            else:
                db.execute("DELETE FROM ip_bans WHERE ip=?", (address,))
            db.commit()
        refresh_banned_ips(self.db_path)
        if banned:
            for token, active_user in list(ACTIVE_SESSIONS.items()):
                if active_user == member_id:
                    ACTIVE_SESSIONS.pop(token, None)
                    ACTIVE_SESSION_SEEN.pop(token, None)
        return {"ok": True, "ip": address, "banned": banned}

    def create_signup_code(self, data: dict[str, object]) -> dict[str, object]:
        code = normalize_signup_code(data.get("code", ""))
        label = str(data.get("label", "")).strip()[:60]
        if not code:
            code = f"CANDY{secrets.token_hex(4).upper()}"
        if not re.fullmatch(r"[A-Za-z0-9]{3,32}", code):
            raise ValueError("가입코드는 3~32자의 영문 대소문자와 숫자로 입력해주세요.")
        with self.support_db() as db:
            try:
                db.execute(
                    """INSERT INTO signup_codes(code,label,active,used_by,used_at,created_at)
                       VALUES(?,?,1,'','',?)""",
                    (code, label, now_text()),
                )
                db.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError("이미 등록된 가입코드입니다.") from exc
        return {"ok": True, "code": code}

    def set_signup_code_active(self, data: dict[str, object]) -> dict[str, object]:
        code = normalize_signup_code(data.get("code", ""))
        active = bool(data.get("active"))
        with self.support_db() as db:
            row = db.execute("SELECT 1 FROM signup_codes WHERE code=?", (code,)).fetchone()
            if row is None:
                raise LookupError("가입코드를 찾을 수 없습니다.")
            db.execute("UPDATE signup_codes SET active=? WHERE code=?", (1 if active else 0, code))
            db.commit()
        return {"ok": True}

    def send_candy_gift(self, data: dict[str, object]) -> dict[str, object]:
        member_id = str(data.get("memberId", "")).strip()[:100]
        influencer_id = str(data.get("influencerId", "")).strip()[:100]
        message = str(data.get("message", "")).strip()[:1000]
        try:
            amount = int(str(data.get("amount", "0")).replace(",", ""))
        except ValueError as exc:
            raise ValueError("캔디 갯수는 숫자로 입력해주세요.") from exc
        if not member_id or not influencer_id:
            raise ValueError("회원과 보내는 BJ를 선택해주세요.")
        if not message:
            raise ValueError("메시지를 입력해주세요.")
        if not 1 <= amount <= MAX_CANDY_BALANCE:
            raise ValueError("캔디 갯수는 1부터 9,999,999,999까지 입력할 수 있습니다.")
        profiles = member_chat_profiles(self.root / "chatlist.php.html")
        if influencer_id not in profiles:
            raise ValueError("선택한 BJ를 찾을 수 없습니다.")
        created_at = now_text()
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            member = db.execute("SELECT id FROM users WHERE id=?", (member_id,)).fetchone()
            if member is None:
                raise LookupError("회원을 찾을 수 없습니다.")
            current = db.execute(
                "SELECT balance FROM wallets WHERE member_id=?",
                (member_id,),
            ).fetchone()
            current_balance = int(current["balance"] or 0) if current else 0
            new_balance = current_balance + amount
            if new_balance > MAX_CANDY_BALANCE:
                raise ValueError("선물 후 캔디 잔고가 허용 범위를 초과합니다.")
            db.execute(
                """INSERT INTO wallets(member_id,balance) VALUES(?,?)
                   ON CONFLICT(member_id) DO UPDATE SET balance=excluded.balance""",
                (member_id, new_balance),
            )
            db.execute("UPDATE users SET balance=? WHERE id=?", (new_balance, member_id))
            db.execute(
                """INSERT INTO chat_messages(
                       sender_id,receiver_id,member_id,influencer_id,message,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (influencer_id, member_id, member_id, influencer_id, message, created_at),
            )
            self.touch_member_chat_room(db, member_id, influencer_id, created_at)
            db.execute(
                """INSERT INTO candy_gifts(admin_id,member_id,influencer_id,message,amount,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (ADMIN_ID, member_id, influencer_id, message, amount, created_at),
            )
            db.commit()
        return {"ok": True, "balance": new_balance, "createdAt": created_at}

    def send_admin_member_chat_message(self, data: dict[str, object]) -> dict[str, object]:
        member_id = str(data.get("memberId", "")).strip()[:100]
        influencer_id = str(data.get("influencerId", "")).strip()[:100]
        if not member_id or not influencer_id:
            raise ValueError("회원과 보내는 BJ를 선택해주세요.")
        profiles = member_chat_profiles(self.root / "chatlist.php.html")
        if influencer_id not in profiles:
            raise ValueError("선택한 BJ를 찾을 수 없습니다.")
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            member = db.execute("SELECT id FROM users WHERE id=?", (member_id,)).fetchone()
            if member is None:
                raise LookupError("회원을 찾을 수 없습니다.")
            payload = self.add_member_chat_message(
                db,
                member_id,
                influencer_id,
                influencer_id,
                data,
            )
            db.commit()
        return payload

    def request_data(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 3 * 1024 * 1024:
            self.rfile.read(length)
            return {}
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        if content_type.lower().startswith("application/json"):
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {}
            return value if isinstance(value, dict) else {}
        if content_type.lower().startswith("multipart/form-data"):
            return parse_multipart(content_type, body)
        return {
            key: values[0] if values else ""
            for key, values in parse_qs(body.decode("utf-8", errors="ignore")).items()
        }

    def support_db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def member_chat_profile(self, influencer_id: str) -> dict[str, str]:
        profiles = member_chat_profiles(self.root / "chatlist.php.html")
        profile = profiles.get(influencer_id)
        if profile is not None:
            return profile
        display_name = self.display_name(influencer_id)
        return {
            "id": influencer_id,
            "name": display_name or influencer_id,
            "nickname": display_name or influencer_id,
            "image": "/img/no_profile.gif",
        }

    @staticmethod
    def touch_member_chat_room(
        db: sqlite3.Connection,
        member_id: str,
        influencer_id: str,
        updated_at: str | None = None,
    ) -> None:
        if not member_id or not influencer_id or member_id == influencer_id:
            return
        if member_id.startswith("live:") or influencer_id.startswith("live:"):
            return
        timestamp = updated_at or now_text()
        db.execute(
            """INSERT INTO member_chat_rooms(member_id,influencer_id,last_at,created_at)
               VALUES(?,?,?,?)
               ON CONFLICT(member_id,influencer_id)
               DO UPDATE SET last_at=excluded.last_at""",
            (member_id, influencer_id, timestamp, timestamp),
        )

    def add_member_chat_message(
        self,
        db: sqlite3.Connection,
        member_id: str,
        influencer_id: str,
        sender_id: str,
        data: dict[str, object],
    ) -> dict[str, object]:
        message = str(data.get("message", "")).strip()[:1000]
        attachment_name, attachment_type, attachment_data = normalize_support_attachment(
            data.get("attachment")
        )
        if not message and not attachment_data:
            raise ValueError("메시지 또는 이미지를 입력해 주세요.")
        if sender_id not in {member_id, influencer_id}:
            raise ValueError("채팅 발신자 정보가 올바르지 않습니다.")

        receiver_id = influencer_id if sender_id == member_id else member_id
        created_at = now_text()
        created_minute = created_at[:16]
        digest_source = "\0".join((message, attachment_type, attachment_data))
        dedupe_key = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
        cursor = db.execute(
            """INSERT OR IGNORE INTO chat_messages(
                   sender_id,receiver_id,member_id,influencer_id,message,
                   attachment_name,attachment_type,attachment_data,
                   dedupe_key,created_minute,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sender_id,
                receiver_id,
                member_id,
                influencer_id,
                message,
                attachment_name,
                attachment_type,
                attachment_data,
                dedupe_key,
                created_minute,
                created_at,
            ),
        )
        duplicate = cursor.rowcount == 0
        if duplicate:
            existing = db.execute(
                """SELECT id,created_at FROM chat_messages
                   WHERE member_id=? AND influencer_id=? AND sender_id=?
                     AND dedupe_key=? AND created_minute=?""",
                (member_id, influencer_id, sender_id, dedupe_key, created_minute),
            ).fetchone()
            message_id = int(existing["id"])
            room_timestamp = str(existing["created_at"])
        else:
            message_id = int(cursor.lastrowid)
            room_timestamp = created_at
        self.touch_member_chat_room(db, member_id, influencer_id, room_timestamp)
        return {
            "ok": True,
            "id": message_id,
            "createdAt": room_timestamp,
            "duplicate": duplicate,
        }

    def send_member_chat_message(
        self,
        member_id: str,
        data: dict[str, object],
    ) -> dict[str, object]:
        influencer_id = str(data.get("influencerId", "")).strip()[:100]
        profiles = member_chat_profiles(self.root / "chatlist.php.html")
        if not influencer_id or influencer_id not in profiles:
            raise ValueError("선택한 BJ를 찾을 수 없습니다.")
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            member = db.execute("SELECT id FROM users WHERE id=?", (member_id,)).fetchone()
            if member is None:
                raise LookupError("회원을 찾을 수 없습니다.")
            payload = self.add_member_chat_message(
                db,
                member_id,
                influencer_id,
                member_id,
                data,
            )
            db.commit()
        return payload

    @staticmethod
    def refresh_member_chat_room(
        db: sqlite3.Connection,
        member_id: str,
        influencer_id: str,
    ) -> None:
        room = db.execute(
            """SELECT created_at FROM member_chat_rooms
               WHERE member_id=? AND influencer_id=?""",
            (member_id, influencer_id),
        ).fetchone()
        if room is None:
            return
        latest = db.execute(
            """SELECT created_at FROM chat_messages
               WHERE member_id=? AND influencer_id=? AND deleted_by_member=0
               ORDER BY id DESC LIMIT 1""",
            (member_id, influencer_id),
        ).fetchone()
        db.execute(
            """UPDATE member_chat_rooms SET last_at=?
               WHERE member_id=? AND influencer_id=?""",
            (
                str(latest["created_at"] if latest else room["created_at"]),
                member_id,
                influencer_id,
            ),
        )

    @staticmethod
    def message_id_from_data(data: dict[str, object]) -> int:
        try:
            message_id = int(data.get("id", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("메시지 정보가 올바르지 않습니다.") from exc
        if message_id <= 0:
            raise ValueError("메시지 정보가 올바르지 않습니다.")
        return message_id

    def delete_member_chat_message(
        self,
        member_id: str,
        data: dict[str, object],
    ) -> dict[str, object]:
        message_id = self.message_id_from_data(data)
        influencer_id = str(data.get("influencerId", "")).strip()[:100]
        if not influencer_id:
            raise ValueError("BJ 정보가 올바르지 않습니다.")
        deleted_at = now_text()
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id FROM chat_messages
                   WHERE id=? AND member_id=? AND influencer_id=?
                     AND sender_id=? AND deleted_by_member=0""",
                (message_id, member_id, influencer_id, member_id),
            ).fetchone()
            if row is None:
                raise LookupError("삭제할 메시지를 찾을 수 없습니다.")
            db.execute(
                """UPDATE chat_messages
                   SET message='',attachment_name='',attachment_type='',attachment_data='',
                       dedupe_key='',deleted_by_member=1,deleted_at=?
                   WHERE id=?""",
                (deleted_at, message_id),
            )
            self.refresh_member_chat_room(db, member_id, influencer_id)
            db.commit()
        return {"ok": True, "deletedAt": deleted_at}

    def edit_admin_member_chat_message(self, data: dict[str, object]) -> dict[str, object]:
        message_id = self.message_id_from_data(data)
        member_id = str(data.get("memberId", "")).strip()[:100]
        influencer_id = str(data.get("influencerId", "")).strip()[:100]
        message = str(data.get("message", "")).strip()[:1000]
        if not member_id or not influencer_id:
            raise ValueError("회원과 BJ 정보가 올바르지 않습니다.")
        if not message:
            raise ValueError("메시지를 입력해 주세요.")
        edited_at = now_text()
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id FROM chat_messages
                   WHERE id=? AND member_id=? AND influencer_id=?
                     AND deleted_by_member=0""",
                (message_id, member_id, influencer_id),
            ).fetchone()
            if row is None:
                raise LookupError("수정할 메시지를 찾을 수 없습니다.")
            db.execute(
                """UPDATE chat_messages SET message=?,edited_at=?,edited_by=? WHERE id=?""",
                (message, edited_at, ADMIN_ID, message_id),
            )
            self.refresh_member_chat_room(db, member_id, influencer_id)
            db.commit()
        return {"ok": True, "editedAt": edited_at}

    def delete_admin_member_chat_message(self, data: dict[str, object]) -> dict[str, object]:
        message_id = self.message_id_from_data(data)
        member_id = str(data.get("memberId", "")).strip()[:100]
        influencer_id = str(data.get("influencerId", "")).strip()[:100]
        if not member_id or not influencer_id:
            raise ValueError("회원과 BJ 정보가 올바르지 않습니다.")
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id FROM chat_messages
                   WHERE id=? AND member_id=? AND influencer_id=?""",
                (message_id, member_id, influencer_id),
            ).fetchone()
            if row is None:
                raise LookupError("삭제할 메시지를 찾을 수 없습니다.")
            db.execute("DELETE FROM chat_messages WHERE id=?", (message_id,))
            self.refresh_member_chat_room(db, member_id, influencer_id)
            db.commit()
        return {"ok": True}

    def member_chat_rooms_payload(self, member_id: str) -> dict[str, object]:
        with self.support_db() as db:
            rows = db.execute(
                """SELECT r.influencer_id,
                          COALESCE((
                             SELECT CASE WHEN m.message<>'' THEN m.message ELSE '[이미지]' END
                             FROM chat_messages m
                             WHERE m.member_id=r.member_id
                               AND m.influencer_id=r.influencer_id
                               AND m.deleted_by_member=0
                             ORDER BY m.id DESC LIMIT 1
                          ),'') AS last_message,
                          COALESCE((
                             SELECT m.created_at FROM chat_messages m
                             WHERE m.member_id=r.member_id
                               AND m.influencer_id=r.influencer_id
                               AND m.deleted_by_member=0
                             ORDER BY m.id DESC LIMIT 1
                          ),r.last_at) AS updated_at,
                          (SELECT COUNT(*) FROM chat_messages m
                           WHERE m.member_id=r.member_id
                             AND m.influencer_id=r.influencer_id
                              AND m.sender_id=r.influencer_id
                               AND m.receiver_id=r.member_id
                               AND m.deleted_by_member=0
                               AND m.read_at='') AS unread
                   FROM member_chat_rooms r
                   WHERE r.member_id=? AND r.influencer_id NOT LIKE 'live:%'
                   ORDER BY updated_at DESC,r.influencer_id ASC
                   LIMIT 100""",
                (member_id,),
            ).fetchall()

        rooms: list[dict[str, object]] = []
        unread_total = 0
        for row in rows:
            influencer_id = str(row["influencer_id"])
            profile = self.member_chat_profile(influencer_id)
            unread = int(row["unread"] or 0)
            unread_total += unread
            rooms.append(
                {
                    "id": influencer_id,
                    "name": profile["name"],
                    "nickname": profile["nickname"],
                    "image": profile["image"],
                    "lastMessage": row["last_message"] or "대화를 시작해보세요.",
                    "updatedAt": row["updated_at"],
                    "unread": unread,
                    "href": f"/chat/memo_form.php?me_recv_mb_id={quote(influencer_id)}",
                }
            )
        return {"rooms": rooms, "unread": unread_total}

    def ensure_support_room(self, db: sqlite3.Connection, member_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM support_rooms WHERE member_id=?",
            (member_id,),
        ).fetchone()
        if row is not None:
            return row
        created_at = now_text()
        greeting = "안녕하세요.\n캔디캐스트 고객센터입니다. 무엇을 도와드릴까요?"
        cursor = db.execute(
            """INSERT INTO support_rooms(
                   member_id,status,queue,staff_unread,member_unread,last_message,last_at,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                member_id,
                "open",
                "normal",
                0,
                0,
                greeting.replace("\n", " "),
                created_at,
                created_at,
                created_at,
            ),
        )
        db.execute(
            """INSERT INTO support_messages(
                   room_id,sender_type,sender_id,message,attachment_name,attachment_type,attachment_data,created_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (cursor.lastrowid, "staff", ADMIN_ID, greeting, "", "", "", created_at),
        )
        db.commit()
        return db.execute(
            "SELECT * FROM support_rooms WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()

    def support_messages(self, db: sqlite3.Connection, room_id: int) -> list[dict[str, object]]:
        rows = db.execute(
            """SELECT id,sender_type,sender_id,message,attachment_name,attachment_type,
                      attachment_data,created_at,edited_at,deleted_by_member
               FROM support_messages WHERE room_id=? ORDER BY id ASC LIMIT 1000""",
            (room_id,),
        ).fetchall()
        return [support_message_payload(row) for row in rows]

    def support_room_payload(self, row: sqlite3.Row) -> dict[str, object]:
        keys = set(row.keys())
        return {
            "id": row["id"],
            "memberId": row["member_id"],
            "status": row["status"],
            "queue": row["queue"] if row["queue"] in SUPPORT_QUEUES else "normal",
            "staffUnread": int(row["staff_unread"] or 0),
            "memberUnread": int(row["member_unread"] or 0),
            "lastMessage": row["last_message"],
            "lastAt": row["last_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "nickname": row["nickname"] if "nickname" in keys and row["nickname"] else row["member_id"],
            "name": row["name"] if "name" in keys and row["name"] else "",
            "phone": row["phone"] if "phone" in keys and row["phone"] else "",
            "balance": int(row["balance"] or 0) if "balance" in keys else 0,
        }

    def member_support_payload(self, member_id: str, mark_read: bool) -> dict[str, object]:
        with self.support_db() as db:
            room = self.ensure_support_room(db, member_id)
            if mark_read and int(room["member_unread"] or 0) > 0:
                read_at = now_text()
                db.execute(
                    "UPDATE support_rooms SET member_unread=0 WHERE id=?",
                    (room["id"],),
                )
                db.execute(
                    """UPDATE support_messages SET read_at=?
                       WHERE room_id=? AND sender_type='staff' AND read_at=''""",
                    (read_at, room["id"]),
                )
                db.commit()
                room = db.execute("SELECT * FROM support_rooms WHERE id=?", (room["id"],)).fetchone()
            return {
                "room": self.support_room_payload(room),
                "messages": self.support_messages(db, int(room["id"])),
            }

    def admin_support_rooms_payload(self) -> list[dict[str, object]]:
        with self.support_db() as db:
            rows = db.execute(
                """SELECT r.*,u.nickname,u.name,u.phone,COALESCE(w.balance,0) AS balance
                   FROM support_rooms r
                   LEFT JOIN users u ON u.id=r.member_id
                   LEFT JOIN wallets w ON w.member_id=r.member_id
                   ORDER BY CASE WHEN r.status='open' THEN 0 ELSE 1 END,
                            CASE WHEN r.staff_unread>0 THEN 0 ELSE 1 END,
                            r.staff_unread DESC,r.updated_at DESC,r.id DESC"""
            ).fetchall()
        return [self.support_room_payload(row) for row in rows]

    def admin_support_room_payload(self, room_id: int, mark_read: bool = True) -> dict[str, object] | None:
        with self.support_db() as db:
            row = db.execute(
                """SELECT r.*,u.nickname,u.name,u.phone,COALESCE(w.balance,0) AS balance
                   FROM support_rooms r
                   LEFT JOIN users u ON u.id=r.member_id
                   LEFT JOIN wallets w ON w.member_id=r.member_id
                   WHERE r.id=?""",
                (room_id,),
            ).fetchone()
            if row is None:
                return None
            if mark_read and int(row["staff_unread"] or 0) > 0:
                read_at = now_text()
                db.execute("UPDATE support_rooms SET staff_unread=0 WHERE id=?", (room_id,))
                db.execute(
                    """UPDATE support_messages SET read_at=?
                       WHERE room_id=? AND sender_type='member' AND read_at=''""",
                    (read_at, room_id),
                )
                db.commit()
                row = db.execute(
                    """SELECT r.*,u.nickname,u.name,u.phone,COALESCE(w.balance,0) AS balance
                       FROM support_rooms r
                       LEFT JOIN users u ON u.id=r.member_id
                       LEFT JOIN wallets w ON w.member_id=r.member_id
                       WHERE r.id=?""",
                    (room_id,),
                ).fetchone()
            return {
                "room": self.support_room_payload(row),
                "messages": self.support_messages(db, room_id),
            }

    def add_support_message(
        self,
        room_id: int,
        sender_type: str,
        sender_id: str,
        data: dict[str, object],
    ) -> tuple[int, str]:
        message = str(data.get("message", "")).strip()[:SUPPORT_MAX_MESSAGE_LENGTH]
        attachment_name, attachment_type, attachment_data = normalize_support_attachment(
            data.get("attachment")
        )
        if not message and not attachment_data:
            raise ValueError("메시지 또는 이미지를 입력해 주세요.")
        created_at = now_text()
        preview = message.replace("\n", " ") if message else "[이미지]"
        with self.support_db() as db:
            room = db.execute("SELECT * FROM support_rooms WHERE id=?", (room_id,)).fetchone()
            if room is None:
                raise LookupError("상담방을 찾을 수 없습니다.")
            cursor = db.execute(
                """INSERT INTO support_messages(
                       room_id,sender_type,sender_id,message,attachment_name,attachment_type,attachment_data,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    room_id,
                    sender_type,
                    sender_id,
                    message,
                    attachment_name,
                    attachment_type,
                    attachment_data,
                    created_at,
                ),
            )
            if sender_type == "member":
                db.execute(
                    """UPDATE support_rooms
                       SET status='open',queue=CASE WHEN status='closed' THEN 'normal' ELSE queue END,
                           staff_unread=staff_unread+1,last_message=?,last_at=?,updated_at=?
                       WHERE id=?""",
                    (preview, created_at, created_at, room_id),
                )
            else:
                db.execute(
                    """UPDATE support_rooms
                       SET status='open',member_unread=member_unread+1,last_message=?,last_at=?,updated_at=?
                       WHERE id=?""",
                    (preview, created_at, created_at, room_id),
                )
            db.commit()
            return int(cursor.lastrowid), created_at

    @staticmethod
    def refresh_support_room(db: sqlite3.Connection, room_id: int) -> None:
        room = db.execute(
            "SELECT created_at FROM support_rooms WHERE id=?",
            (room_id,),
        ).fetchone()
        if room is None:
            return
        latest = db.execute(
            """SELECT message,attachment_data,created_at FROM support_messages
               WHERE room_id=? AND deleted_by_member=0
               ORDER BY id DESC LIMIT 1""",
            (room_id,),
        ).fetchone()
        if latest is None:
            preview = ""
            last_at = str(room["created_at"])
        else:
            preview = str(latest["message"] or "").replace("\n", " ")
            if not preview and latest["attachment_data"]:
                preview = "[이미지]"
            last_at = str(latest["created_at"])
        staff_unread = int(
            db.execute(
                """SELECT COUNT(*) FROM support_messages
                   WHERE room_id=? AND sender_type='member'
                     AND read_at='' AND deleted_by_member=0""",
                (room_id,),
            ).fetchone()[0]
        )
        member_unread = int(
            db.execute(
                """SELECT COUNT(*) FROM support_messages
                   WHERE room_id=? AND sender_type='staff'
                     AND read_at='' AND deleted_by_member=0""",
                (room_id,),
            ).fetchone()[0]
        )
        db.execute(
            """UPDATE support_rooms
               SET last_message=?,last_at=?,updated_at=?,staff_unread=?,member_unread=?
               WHERE id=?""",
            (preview, last_at, now_text(), staff_unread, member_unread, room_id),
        )

    def delete_member_support_message(
        self,
        member_id: str,
        data: dict[str, object],
    ) -> dict[str, object]:
        message_id = self.message_id_from_data(data)
        deleted_at = now_text()
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT m.id,m.room_id FROM support_messages m
                   JOIN support_rooms r ON r.id=m.room_id
                   WHERE m.id=? AND r.member_id=? AND m.sender_type='member'
                     AND m.sender_id=? AND m.deleted_by_member=0""",
                (message_id, member_id, member_id),
            ).fetchone()
            if row is None:
                raise LookupError("삭제할 메시지를 찾을 수 없습니다.")
            room_id = int(row["room_id"])
            db.execute(
                """UPDATE support_messages
                   SET message='',attachment_name='',attachment_type='',attachment_data='',
                       deleted_by_member=1,deleted_at=? WHERE id=?""",
                (deleted_at, message_id),
            )
            self.refresh_support_room(db, room_id)
            db.commit()
        return {"ok": True, "deletedAt": deleted_at}

    def edit_admin_support_message(
        self,
        room_id: int,
        data: dict[str, object],
    ) -> dict[str, object]:
        message_id = self.message_id_from_data(data)
        message = str(data.get("message", "")).strip()[:SUPPORT_MAX_MESSAGE_LENGTH]
        if not message:
            raise ValueError("메시지를 입력해 주세요.")
        edited_at = now_text()
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id FROM support_messages
                   WHERE id=? AND room_id=? AND deleted_by_member=0""",
                (message_id, room_id),
            ).fetchone()
            if row is None:
                raise LookupError("수정할 메시지를 찾을 수 없습니다.")
            db.execute(
                """UPDATE support_messages SET message=?,edited_at=?,edited_by=? WHERE id=?""",
                (message, edited_at, ADMIN_ID, message_id),
            )
            self.refresh_support_room(db, room_id)
            db.commit()
        return {"ok": True, "editedAt": edited_at}

    def delete_admin_support_message(
        self,
        room_id: int,
        data: dict[str, object],
    ) -> dict[str, object]:
        message_id = self.message_id_from_data(data)
        with self.support_db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM support_messages WHERE id=? AND room_id=?",
                (message_id, room_id),
            ).fetchone()
            if row is None:
                raise LookupError("삭제할 메시지를 찾을 수 없습니다.")
            db.execute("DELETE FROM support_messages WHERE id=?", (message_id,))
            self.refresh_support_room(db, room_id)
            db.commit()
        return {"ok": True}

    def render_support_admin(self) -> bytes:
        try:
            from bs4 import BeautifulSoup
        except Exception:
            return html_page("고객센터", "<h1>고객센터</h1><p>화면을 불러올 수 없습니다.</p>")
        soup = BeautifulSoup(
            normalize_admin_html(read_text(self.root / "adm" / "index.html")),
            "html.parser",
        )
        remove_legacy_admin_token_script(soup)
        if soup.body is not None:
            body_classes = list(soup.body.get("class", []))
            if "cc-support-admin-page" not in body_classes:
                body_classes.append("cc-support-admin-page")
            soup.body["class"] = body_classes
        if soup.title is not None:
            soup.title.string = "고객센터 | CandyCast 관리자"
        container = soup.select_one("#container") or soup.body
        if container is None:
            return str(soup).encode("utf-8")
        container.clear()
        fragment = BeautifulSoup(SUPPORT_ADMIN_MARKUP, "html.parser")
        for child in list(fragment.contents):
            container.append(child)
        image_script = soup.new_tag(
            "script",
            src="/assets/local/candycast-image-utils.js",
        )
        script = soup.new_tag(
            "script",
            src="/assets/local/candycast-admin-support.js?v=20260717-chat3",
        )
        if soup.body is not None:
            soup.body.append(image_script)
            soup.body.append(script)
        return str(soup).encode("utf-8")

    def render_admin_application(
        self,
        title: str,
        body_class: str,
        markup: str,
        stylesheet: str,
        script: str,
        dependency_scripts: tuple[str, ...] = (),
    ) -> bytes:
        try:
            from bs4 import BeautifulSoup
        except Exception:
            return html_page(title, f"<h1>{html.escape(title)}</h1><p>화면을 불러올 수 없습니다.</p>")
        soup = BeautifulSoup(
            normalize_admin_html(read_text(self.root / "adm" / "index.html")),
            "html.parser",
        )
        remove_legacy_admin_token_script(soup)
        if soup.body is not None:
            body_classes = list(soup.body.get("class", []))
            if body_class not in body_classes:
                body_classes.append(body_class)
            soup.body["class"] = body_classes
        if soup.title is not None:
            soup.title.string = f"{title} | CandyCast 관리자"
        container = soup.select_one("#container") or soup.body
        if container is None:
            return str(soup).encode("utf-8")
        container.clear()
        fragment = BeautifulSoup(markup, "html.parser")
        for child in list(fragment.contents):
            container.append(child)
        if soup.head is not None:
            style = soup.new_tag("link", rel="stylesheet", href=f"{stylesheet}?v=20260718-admin2")
            soup.head.append(style)
        if soup.body is not None:
            for dependency in dependency_scripts:
                soup.body.append(soup.new_tag("script", src=dependency))
            application_script = soup.new_tag("script", src=f"{script}?v=20260718-admin2")
            soup.body.append(application_script)
        return str(soup).encode("utf-8")

    def render_members_admin(self) -> bytes:
        return self.render_admin_application(
            "회원 개인정보/등급 관리",
            "cc-members-admin-page",
            MEMBERS_ADMIN_MARKUP,
            "/assets/local/candycast-admin-members.css",
            "/assets/local/candycast-admin-members.js",
        )

    def render_partners_admin(self) -> bytes:
        return self.render_admin_application(
            "파트너 추가",
            "cc-partners-admin-page",
            PARTNERS_ADMIN_MARKUP,
            "/assets/local/candycast-admin-members.css",
            "/assets/local/candycast-admin-members.js",
        )

    def render_member_chat_admin(self) -> bytes:
        return self.render_admin_application(
            "개인 채팅",
            "cc-member-chat-admin-page",
            MEMBER_CHAT_ADMIN_MARKUP,
            "/assets/local/candycast-admin-member-chat.css",
            "/assets/local/candycast-admin-member-chat.js",
            ("/assets/local/candycast-image-utils.js",),
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == LEGACY_ADMIN_PREFIX or path.startswith(f"{LEGACY_ADMIN_PREFIX}/"):
            location = canonical_admin_path(path)
            if parsed.query:
                location = f"{location}?{parsed.query}"
            self.send_redirect(location)
            return
        path = canonical_admin_path(path)
        if path == ADMIN_PREFIX:
            location = f"{ADMIN_PREFIX}/"
            if parsed.query:
                location = f"{location}?{parsed.query}"
            self.send_redirect(location)
            return
        if self.block_banned_request(path):
            return
        legacy_notice = ""
        if path == "/bbs/register_form.php" and query.get("message", [""])[0]:
            legacy_notice = query["message"][0]
        elif path == "/bbs/login.php" and query.get("registered") == ["1"]:
            legacy_notice = "회원가입이 완료되었습니다. 새 계정으로 로그인해주세요."
        elif path == "/bbs/login.php" and query.get("error") == ["1"]:
            legacy_notice = LOGIN_ERROR_MESSAGE
        if legacy_notice:
            self.send_redirect(path, [make_flash_cookie(legacy_notice)])
            return
        flash_notice = ""
        flash_response_cookies: list[str] = []
        if path in {"/bbs/register_form.php", "/bbs/login.php"}:
            encoded_notice = self.cookie_value(FLASH_COOKIE)
            if encoded_notice:
                flash_notice = unquote(encoded_notice)[:500]
                flash_response_cookies.append(make_flash_cookie("", 0))
        current_user = self.current_user()
        logged_in = bool(current_user)
        display_name = self.display_name(current_user)
        balance = self.balance(current_user)
        member_state = self.member_state(current_user)
        profile_image_url = str(member_state["profile_image_url"])
        if path == PROFILE_MEDIA_PATH:
            self.send_member_profile_image(current_user)
            return
        if path == "/bbs/logout.php":
            token = self.session_token()
            if token:
                ACTIVE_SESSIONS.pop(token, None)
                ACTIVE_SESSION_SEEN.pop(token, None)
            self.send_redirect(
                "/",
                [f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"],
            )
            return
        if path == "/bbs/login.php" and logged_in:
            self.send_redirect("/")
            return
        if is_admin_path(path) and current_user != ADMIN_ID:
            login_target = quote(self.path if self.path.startswith("/") else f"{ADMIN_PREFIX}/")
            self.send_redirect(f"/bbs/login.php?url={login_target}")
            return
        if path in {
            f"{ADMIN_PREFIX}/",
            f"{ADMIN_PREFIX}/index.html",
            f"{ADMIN_PREFIX}/index.php",
        }:
            self.send_redirect(f"{ADMIN_PREFIX}/members")
            return
        if path.startswith("/api/admin/") and current_user != ADMIN_ID:
            self.send_json({"error": "관리자 로그인이 필요합니다."}, HTTPStatus.FORBIDDEN)
            return
        if path in {
            f"{ADMIN_PREFIX}/member_list.php",
            f"{ADMIN_PREFIX}/member_list.php.html",
        }:
            self.send_redirect(f"{ADMIN_PREFIX}/members")
            return
        if path in {
            f"{ADMIN_PREFIX}/regist_code.php",
            f"{ADMIN_PREFIX}/regist_code.php.html",
            f"{ADMIN_PREFIX}/partner_add.php",
            f"{ADMIN_PREFIX}/partner_add.php.html",
        }:
            self.send_redirect(f"{ADMIN_PREFIX}/partners")
            return
        if path in {
            f"{ADMIN_PREFIX}/chat_list.php",
            f"{ADMIN_PREFIX}/chat_list.php.html",
            f"{ADMIN_PREFIX}/chat_room.php",
            f"{ADMIN_PREFIX}/chat_room.php.html",
        }:
            self.send_redirect(f"{ADMIN_PREFIX}/chats")
            return
        if path == f"{ADMIN_PREFIX}/members":
            self.send_bytes(self.render_members_admin())
            return
        if path == f"{ADMIN_PREFIX}/partners":
            self.send_bytes(self.render_partners_admin())
            return
        if path == f"{ADMIN_PREFIX}/chats":
            self.send_bytes(self.render_member_chat_admin())
            return
        if path == "/api/admin/members":
            self.send_json(self.admin_members_payload(query.get("q", [""])[0]))
            return
        if path == "/api/admin/transactions":
            self.send_json(self.admin_transactions_payload(query))
            return
        if path == "/api/admin/signup-codes":
            self.send_json(self.admin_signup_codes_payload())
            return
        if path == "/api/admin/influencers":
            self.send_json(self.admin_influencers_payload(query.get("q", [""])[0]))
            return
        if path == "/api/admin/member-chat/rooms":
            self.send_json(self.admin_member_chat_rooms_payload(query.get("q", [""])[0]))
            return
        if path == "/api/admin/member-chat/messages":
            member_id = query.get("member_id", [""])[0].strip()[:100]
            influencer_id = query.get("influencer_id", [""])[0].strip()[:100]
            if not member_id or not influencer_id:
                self.send_json(
                    {"error": "회원과 BJ를 선택해주세요."},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            payload = self.admin_member_chat_payload(member_id, influencer_id)
            if payload is None:
                self.send_json(
                    {"error": "회원 또는 BJ를 찾을 수 없습니다."},
                    HTTPStatus.NOT_FOUND,
                )
            else:
                self.send_json(payload)
            return
        if path == "/api/member/chats":
            if not current_user:
                self.send_json({"error": "로그인이 필요합니다."}, HTTPStatus.UNAUTHORIZED)
                return
            restriction = self.member_action_restriction(current_user)
            if restriction:
                self.send_json({"error": restriction}, HTTPStatus.LOCKED)
                return
            self.send_json(self.member_chat_rooms_payload(current_user))
            return
        if path == "/api/support/unread":
            if not current_user:
                self.send_json({"error": "로그인이 필요합니다."}, HTTPStatus.UNAUTHORIZED)
                return
            with self.support_db() as db:
                row = db.execute(
                    "SELECT member_unread FROM support_rooms WHERE member_id=?",
                    (current_user,),
                ).fetchone()
            self.send_json({"unread": int(row[0] or 0) if row else 0})
            return
        if path == "/api/support/room":
            if not current_user:
                self.send_json({"error": "로그인이 필요합니다."}, HTTPStatus.UNAUTHORIZED)
                return
            self.send_json(
                self.member_support_payload(
                    current_user,
                    mark_read=query.get("mark_read", ["0"])[0] == "1",
                )
            )
            return
        if path == "/api/admin/support/unread":
            if current_user != ADMIN_ID:
                self.send_json({"error": "관리자 로그인이 필요합니다."}, HTTPStatus.FORBIDDEN)
                return
            with self.support_db() as db:
                unread = db.execute(
                    "SELECT COALESCE(SUM(staff_unread),0) FROM support_rooms WHERE status='open'"
                ).fetchone()[0]
            self.send_json({"unread": int(unread or 0)})
            return
        if path == "/api/admin/support/rooms":
            if current_user != ADMIN_ID:
                self.send_json({"error": "관리자 로그인이 필요합니다."}, HTTPStatus.FORBIDDEN)
                return
            self.send_json({"rooms": self.admin_support_rooms_payload()})
            return
        support_room_match = re.fullmatch(r"/api/admin/support/rooms/(\d+)", path)
        if support_room_match:
            if current_user != ADMIN_ID:
                self.send_json({"error": "관리자 로그인이 필요합니다."}, HTTPStatus.FORBIDDEN)
                return
            room_payload = self.admin_support_room_payload(int(support_room_match.group(1)))
            if room_payload is None:
                self.send_json({"error": "상담방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            else:
                self.send_json(room_payload)
            return
        if path == f"{ADMIN_PREFIX}/support":
            self.send_bytes(self.render_support_admin())
            return
        if path in {"", "/"}:
            index_path = self.root / "index.html"
            if index_path.is_file():
                self.send_bytes(
                    render_dynamic_home(
                        index_path,
                        logged_in=logged_in,
                        current_user=current_user,
                        display_name=display_name,
                        balance=balance,
                        balance_status=str(member_state["balance_status"]),
                        account_status=str(member_state["account_status"]),
                        display_grade=str(member_state["display_grade"]),
                        profile_image_url=profile_image_url,
                    )
                )
                return
        if path == "/chatlist.php":
            chatlist_path = self.root / "chatlist.php.html"
            if chatlist_path.is_file():
                self.send_bytes(
                    render_dynamic_page(
                        chatlist_path,
                        logged_in=logged_in,
                        current_user=current_user,
                        display_name=display_name,
                        balance=balance,
                        balance_status=str(member_state["balance_status"]),
                        account_status=str(member_state["account_status"]),
                        display_grade=str(member_state["display_grade"]),
                        profile_image_url=profile_image_url,
                    )
                )
                return
        if path in {"/my.php", "/my2.php"} and not logged_in:
            self.send_redirect(f"/bbs/login.php?url={quote(path, safe='')}")
            return
        if path == "/my2.php":
            self.send_bytes(
                self.render_my2(
                    current_user,
                    display_name,
                    balance,
                    str(member_state["balance_status"]),
                    str(member_state["account_status"]),
                    str(member_state["display_grade"]),
                    profile_image_url,
                )
            )
            return
        if path == "/live.php" and query.get("live_id", [""])[0]:
            archive = self.root / f"live__q_{query_hash(parsed.query)}.html"
            if not archive.is_file():
                archive = next(iter(sorted(self.root.glob("live__q_*.html"))), self.root / "index.html")
            self.send_bytes(
                render_dynamic_page(
                    archive,
                    logged_in=logged_in,
                    current_user=current_user,
                    display_name=display_name,
                    balance=balance,
                    balance_status=str(member_state["balance_status"]),
                    account_status=str(member_state["account_status"]),
                    display_grade=str(member_state["display_grade"]),
                    profile_image_url=profile_image_url,
                )
            )
            return
        archive_match = re.fullmatch(r"/live__q_([0-9a-f]{12})\.html", path)
        if archive_match:
            archive = self.root / path.lstrip("/")
            if not archive.is_file():
                archive = next(iter(sorted(self.root.glob("live__q_*.html"))), self.root / "index.html")
            self.send_bytes(
                render_dynamic_page(
                    archive,
                    logged_in=logged_in,
                    current_user=current_user,
                    display_name=display_name,
                    balance=balance,
                    balance_status=str(member_state["balance_status"]),
                    account_status=str(member_state["account_status"]),
                    display_grade=str(member_state["display_grade"]),
                    profile_image_url=profile_image_url,
                )
            )
            return
        if path == "/chat/memo_form.php":
            if not logged_in:
                login_target = quote(self.path if self.path.startswith("/") else "/chatlist.php")
                self.send_redirect(f"/bbs/login.php?url={login_target}")
                return
            receiver = query.get("me_recv_mb_id", [""])[0].strip()[:100]
            if not receiver or receiver.startswith("live:"):
                self.send_redirect("/chatlist.php")
                return
            self.send_bytes(self.render_chat(receiver, current_user))
            return
        maintenance = re.fullmatch(r"/admin/([^/]+_file_delete\.php)", path)
        if maintenance and maintenance.group(1) in MAINTENANCE_ACTIONS:
            self.send_bytes(
                self.render_maintenance(
                    maintenance.group(1),
                    ran=query.get("run") == ["1"],
                )
            )
            return
        if path == "/export_handler.php":
            if not logged_in:
                message = (
                    '<div class="export-popup-con"><div class="f-header">환전신청</div>'
                    '<p style="padding:20px 0;">로그인 후 이용해주세요.</p></div>'
                )
                self.send_bytes(message.encode("utf-8"), status=HTTPStatus.UNAUTHORIZED)
                return
            restriction = self.member_action_restriction(current_user, require_balance=True)
            if restriction:
                message = (
                    '<div class="export-popup-con"><div class="f-header">이용 제한 안내</div>'
                    f'<p style="padding:20px 0;line-height:1.6;">{html.escape(restriction)}</p></div>'
                )
                self.send_bytes(message.encode("utf-8"), status=HTTPStatus.LOCKED)
                return
            popup = (
                CANDYCAST_EXPORT_POPUP.replace("__BALANCE__", str(balance))
                .replace("__MEMBER_ID__", html.escape(current_user, quote=True))
            )
            self.send_bytes(popup.encode("utf-8"))
            return
        if path in {"/admin/export_list.php", "/admin/export_list.php.html"}:
            self.send_bytes(self.render_transactions("export"))
            return
        if path in {"/admin/import_list.php", "/admin/import_list.php.html"}:
            self.send_bytes(self.render_transactions("import"))
            return
        if path == "/admin/imported_logs.php":
            self.send_bytes(self.render_imported_logs())
            return
        if path == "/member/find/findId":
            self.send_bytes(html_page("아이디 찾기", "<h1>아이디 찾기 / 비밀번호 찾기</h1><p>원본 DB 없이 분리된 로컬 복제본입니다. 계정 복구는 새 운영 DB 연결 후 활성화하세요.</p><p><a href='/bbs/login.php'>로그인으로 돌아가기</a></p>"))
            return
        if path == "/bbs/formdata.php":
            self.send_bytes(b"0", "text/plain; charset=utf-8")
            return
        if path == "/bbs/login_check.php":
            self.send_redirect("/bbs/login.php")
            return
        if path == "/bbs/register_form_update.php":
            self.send_redirect("/bbs/register_form.php")
            return
        self.serve_static(
            path,
            logged_in=logged_in,
            login_error=flash_notice == LOGIN_ERROR_MESSAGE,
            current_user=current_user,
            display_name=display_name,
            balance=balance,
            balance_status=str(member_state["balance_status"]),
            account_status=str(member_state["account_status"]),
            display_grade=str(member_state["display_grade"]),
            profile_image_url=profile_image_url,
            login_target=query.get("url", ["/"])[0],
            notice=("" if flash_notice == LOGIN_ERROR_MESSAGE else flash_notice) or (
                "회원가입이 완료되었습니다. 새 계정으로 로그인해주세요."
                if query.get("registered") == ["1"]
                else query.get("message", [""])[0]
            ),
            cookies=flash_response_cookies,
        )

    def do_HEAD(self) -> None:  # noqa: N802
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = canonical_admin_path(parsed.path)
        data = self.request_data()
        if self.block_banned_request(path, data):
            return
        if path in {"/api/member/profile", "/api/member/profile/delete"}:
            current_user = self.current_user()
            if not current_user or current_user == ADMIN_ID:
                self.send_json({"error": "로그인이 필요합니다."}, HTTPStatus.UNAUTHORIZED)
                return
            restriction = self.member_action_restriction(current_user)
            if restriction:
                self.send_json({"error": restriction}, HTTPStatus.LOCKED)
                return
            try:
                payload = (
                    self.save_member_profile(current_user, data)
                    if path == "/api/member/profile"
                    else self.delete_member_profile(current_user)
                )
                self.send_json(payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path == "/api/member/chat/messages":
            current_user = self.current_user()
            if not current_user or current_user == ADMIN_ID:
                self.send_json({"error": "로그인이 필요합니다."}, HTTPStatus.UNAUTHORIZED)
                return
            restriction = self.member_action_restriction(current_user)
            if restriction:
                self.send_json({"error": restriction}, HTTPStatus.LOCKED)
                return
            try:
                self.send_json(
                    self.send_member_chat_message(current_user, data),
                    HTTPStatus.CREATED,
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path == "/api/member/chat/messages/delete":
            current_user = self.current_user()
            if not current_user or current_user == ADMIN_ID:
                self.send_json({"error": "로그인이 필요합니다."}, HTTPStatus.UNAUTHORIZED)
                return
            restriction = self.member_action_restriction(current_user)
            if restriction:
                self.send_json({"error": restriction}, HTTPStatus.LOCKED)
                return
            try:
                self.send_json(self.delete_member_chat_message(current_user, data))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path.startswith("/api/admin/") and self.current_user() != ADMIN_ID:
            self.send_json({"error": "관리자 로그인이 필요합니다."}, HTTPStatus.FORBIDDEN)
            return
        if path == "/api/admin/members/update":
            try:
                self.send_json(self.update_member_from_admin(data))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except sqlite3.IntegrityError:
                self.send_json({"error": "회원 정보를 저장할 수 없습니다."}, HTTPStatus.CONFLICT)
            return
        if path == "/api/admin/members/ip-ban":
            try:
                self.send_json(self.set_member_ip_ban(data))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path == "/api/admin/transactions/action":
            try:
                self.send_json(self.admin_transaction_action(data))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path == "/api/admin/transactions/account":
            try:
                self.send_json(self.update_transaction_account(data))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path == "/api/admin/signup-codes/create":
            try:
                self.send_json(self.create_signup_code(data), HTTPStatus.CREATED)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/admin/signup-codes/toggle":
            try:
                self.send_json(self.set_signup_code_active(data))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path == "/api/admin/gifts":
            try:
                self.send_json(self.send_candy_gift(data), HTTPStatus.CREATED)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path == "/api/admin/member-chat/messages":
            try:
                self.send_json(self.send_admin_member_chat_message(data), HTTPStatus.CREATED)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path in {
            "/api/admin/member-chat/messages/edit",
            "/api/admin/member-chat/messages/delete",
        }:
            try:
                payload = (
                    self.edit_admin_member_chat_message(data)
                    if path.endswith("/edit")
                    else self.delete_admin_member_chat_message(data)
                )
                self.send_json(payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path == "/api/support/messages":
            current_user = self.current_user()
            if not current_user:
                self.send_json({"error": "로그인이 필요합니다."}, HTTPStatus.UNAUTHORIZED)
                return
            try:
                with self.support_db() as db:
                    room = self.ensure_support_room(db, current_user)
                    room_id = int(room["id"])
                message_id, created_at = self.add_support_message(
                    room_id,
                    "member",
                    current_user,
                    data,
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"ok": True, "id": message_id, "createdAt": created_at})
            return
        if path == "/api/support/messages/delete":
            current_user = self.current_user()
            if not current_user or current_user == ADMIN_ID:
                self.send_json({"error": "로그인이 필요합니다."}, HTTPStatus.UNAUTHORIZED)
                return
            restriction = self.member_action_restriction(current_user)
            if restriction:
                self.send_json({"error": restriction}, HTTPStatus.LOCKED)
                return
            try:
                self.send_json(self.delete_member_support_message(current_user, data))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        if path == "/api/support/read":
            current_user = self.current_user()
            if not current_user:
                self.send_json({"error": "로그인이 필요합니다."}, HTTPStatus.UNAUTHORIZED)
                return
            self.member_support_payload(current_user, mark_read=True)
            self.send_json({"ok": True})
            return
        support_action_match = re.fullmatch(
            r"/api/admin/support/rooms/(\d+)/(messages|edit-message|delete-message|queue|close|clear)",
            path,
        )
        if support_action_match:
            if self.current_user() != ADMIN_ID:
                self.send_json({"error": "관리자 로그인이 필요합니다."}, HTTPStatus.FORBIDDEN)
                return
            room_id = int(support_action_match.group(1))
            action = support_action_match.group(2)
            try:
                if action == "messages":
                    message_id, created_at = self.add_support_message(
                        room_id,
                        "staff",
                        ADMIN_ID,
                        data,
                    )
                    self.send_json({"ok": True, "id": message_id, "createdAt": created_at})
                    return
                if action == "edit-message":
                    self.send_json(self.edit_admin_support_message(room_id, data))
                    return
                if action == "delete-message":
                    self.send_json(self.delete_admin_support_message(room_id, data))
                    return
                with self.support_db() as db:
                    room = db.execute("SELECT id FROM support_rooms WHERE id=?", (room_id,)).fetchone()
                    if room is None:
                        raise LookupError("상담방을 찾을 수 없습니다.")
                    if action == "queue":
                        queue = str(data.get("queue", "")).strip()
                        if queue not in SUPPORT_QUEUES:
                            raise ValueError("상담함이 올바르지 않습니다.")
                        db.execute(
                            "UPDATE support_rooms SET queue=?,updated_at=? WHERE id=?",
                            (queue, now_text(), room_id),
                        )
                    elif action == "close":
                        db.execute(
                            """UPDATE support_rooms
                               SET status='closed',staff_unread=0,updated_at=? WHERE id=?""",
                            (now_text(), room_id),
                        )
                    elif action == "clear":
                        cleared_at = now_text()
                        greeting = "안녕하세요.\n캔디캐스트 고객센터입니다. 무엇을 도와드릴까요?"
                        db.execute("DELETE FROM support_messages WHERE room_id=?", (room_id,))
                        db.execute(
                            """INSERT INTO support_messages(
                                   room_id,sender_type,sender_id,message,attachment_name,attachment_type,attachment_data,created_at
                               ) VALUES(?,?,?,?,?,?,?,?)""",
                            (room_id, "staff", ADMIN_ID, greeting, "", "", "", cleared_at),
                        )
                        db.execute(
                            """UPDATE support_rooms SET staff_unread=0,member_unread=0,
                               last_message=?,last_at=?,updated_at=? WHERE id=?""",
                            (greeting.replace("\n", " "), cleared_at, cleared_at, room_id),
                        )
                    db.commit()
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            except LookupError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
                return
            self.send_json({"ok": True})
            return
        if path == "/bbs/formdata.php":
            current_user = self.current_user()
            restriction = self.member_action_restriction(current_user, require_balance=True)
            if restriction:
                self.send_bytes(
                    restriction.encode("utf-8"),
                    "text/plain; charset=utf-8",
                    HTTPStatus.LOCKED,
                )
                return
            self.handle_formdata(data)
            return
        if path == "/bbs/login_check.php":
            username = data.get("mb_id", "").strip()
            password = data.get("mb_password", "")
            valid_login = (
                username == ADMIN_ID and verify_admin_password(password)
            ) or verify_user_password(self.db_path, username, password)
            if valid_login:
                token = secrets.token_urlsafe(32)
                ACTIVE_SESSIONS[token] = username
                ACTIVE_SESSION_SEEN[token] = time.monotonic()
                self.remember_member_ip(username, force=True)
                target = unquote(data.get("url", "/") or "/")
                if not target.startswith("/") or target.startswith("//"):
                    target = "/"
                self.send_redirect(
                    target,
                    [f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax"],
                )
            else:
                self.send_redirect("/bbs/login.php", [make_flash_cookie(LOGIN_ERROR_MESSAGE)])
            return
        if path == "/bbs/register_form_update.php":
            self.handle_registration(data)
            return
        if path == "/chat/memo_form.php":
            current_user = self.current_user()
            receiver = parse_qs(parsed.query).get("me_recv_mb_id", [""])[0].strip()[:100]
            message = data.get("message", "").strip()
            restriction = self.member_action_restriction(current_user)
            if (
                not current_user
                or not receiver
                or receiver.startswith("live:")
                or not message
                or restriction
            ):
                self.send_bytes(
                    (restriction or "0").encode("utf-8"),
                    "text/plain; charset=utf-8",
                    HTTPStatus.LOCKED if restriction else HTTPStatus.BAD_REQUEST,
                )
                return
            try:
                self.send_member_chat_message(
                    current_user,
                    {"influencerId": receiver, "message": message},
                )
            except (ValueError, LookupError) as exc:
                self.send_bytes(
                    str(exc).encode("utf-8"),
                    "text/plain; charset=utf-8",
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self.send_redirect(f"/chat/memo_form.php?me_recv_mb_id={quote(receiver)}")
            return
        if path == "/chat/live_message":
            current_user = self.current_user()
            live_id = parse_qs(parsed.query).get("live_id", [""])[0].strip()
            message = data.get("message", "").strip()
            restriction = self.member_action_restriction(current_user)
            if restriction:
                self.send_bytes(
                    restriction.encode("utf-8"),
                    "text/plain; charset=utf-8",
                    HTTPStatus.LOCKED,
                )
                return
            if current_user and live_id and message:
                with sqlite3.connect(self.db_path) as db:
                    db.execute(
                        "INSERT INTO chat_messages(sender_id,receiver_id,message,created_at) VALUES(?,?,?,?)",
                        (
                            current_user,
                            f"live:{live_id}",
                            message[:500],
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    )
                    db.commit()
            self.send_redirect(f"/live.php?live_id={quote(live_id)}")
            return
        if path == f"{ADMIN_PREFIX}/ajax.token.php":
            if self.current_user() != ADMIN_ID:
                self.send_json({"error": "관리자 로그인이 필요합니다."}, HTTPStatus.FORBIDDEN)
                return
            self.send_json({"token": secrets.token_hex(16)})
            return
        if path == "/admin/transaction_status.php":
            if self.current_user() != ADMIN_ID:
                self.send_bytes(b"Forbidden", "text/plain; charset=utf-8", HTTPStatus.FORBIDDEN)
                return
            try:
                transaction_id = int(data.get("id", "0"))
            except ValueError:
                transaction_id = 0
            status = data.get("status", "")
            kind = data.get("kind", "export")
            if transaction_id > 0 and status in {"대기", "동결", "취소", "승인", "완료"}:
                try:
                    self.set_transaction_status(transaction_id, kind, str(status))
                except (ValueError, LookupError) as exc:
                    self.send_bytes(
                        str(exc).encode("utf-8"),
                        "text/plain; charset=utf-8",
                        HTTPStatus.CONFLICT,
                    )
                    return
            target = "export_list.php" if kind == "export" else "import_list.php"
            self.send_redirect(f"{ADMIN_PREFIX}/{target}")
            return
        self.send_bytes(b"0", "text/plain; charset=utf-8", 404)

    def handle_registration(self, data: dict[str, str]) -> None:
        username = data.get("mb_id", "").strip()
        password = data.get("mb_password", "")
        password_confirm = data.get("mb_password_re", "")
        name = data.get("mb_name", "").strip()
        nickname = data.get("mb_nick", "").strip()
        phone = re.sub(r"[^0-9]", "", data.get("mb_hp", ""))
        signup_code = normalize_signup_code(data.get("chuchu", ""))

        error = ""
        if not signup_code:
            error = "가입코드를 입력해주세요."
        elif not re.fullmatch(r"[A-Za-z0-9]{3,32}", signup_code):
            error = "가입코드 형식이 올바르지 않습니다."
        elif not re.fullmatch(r"[A-Za-z0-9]{6,15}", username):
            error = "아이디는 6~15자의 영문과 숫자로 입력해주세요."
        elif username.lower() == ADMIN_ID.lower():
            error = "사용할 수 없는 아이디입니다."
        elif password != password_confirm:
            error = "비밀번호 확인이 일치하지 않습니다."
        elif not (
            8 <= len(password) <= 15
            and re.search(r"[A-Za-z]", password)
            and re.search(r"[0-9]", password)
            and re.search(r"[^A-Za-z0-9]", password)
        ):
            error = "비밀번호는 8~15자의 영문, 숫자, 특수문자를 포함해야 합니다."
        elif not name:
            error = "이름을 입력해주세요."
        elif not 2 <= len(nickname) <= 7:
            error = "닉네임은 2~7자로 입력해주세요."

        with sqlite3.connect(self.db_path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            code_row = db.execute(
                "SELECT active FROM signup_codes WHERE code=?",
                (signup_code,),
            ).fetchone()
            if not error and (
                code_row is None or int(code_row[0] or 0) != 1
            ):
                error = "가입코드가 올바르지 않거나 현재 중지되었습니다."
            exists = db.execute("SELECT 1 FROM users WHERE id=?", (username,)).fetchone()
            if not error and exists:
                error = "이미 사용 중인 아이디입니다."
            if not error:
                salt, digest = hash_password(password)
                birthday = "-".join(
                    [
                        data.get("birthy", ""),
                        data.get("birthm", "").zfill(2),
                        data.get("birthd", "").zfill(2),
                    ]
                )
                db.execute(
                    """INSERT INTO users(
                           id,password_salt,password_hash,name,nickname,phone,sex,birthday,balance,
                           signup_code,role,display_grade,internal_grade,balance_status,account_status,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        username,
                        salt,
                        digest,
                        name,
                        nickname,
                        phone,
                        data.get("mb_sex", ""),
                        birthday,
                        0,
                        signup_code,
                        "MEMBER",
                        DISPLAY_GRADES[0],
                        1,
                        BALANCE_STATUSES[0],
                        ACCOUNT_STATUSES[0],
                        now_text(),
                    ),
                )
                db.execute(
                    "INSERT INTO wallets(member_id,balance) VALUES(?,0)",
                    (username,),
                )
            db.commit()

        if error:
            self.send_redirect("/bbs/register_form.php", [make_flash_cookie(error)])
        else:
            self.send_redirect(
                "/bbs/login.php",
                [make_flash_cookie("회원가입이 완료되었습니다. 새 계정으로 로그인해주세요.")],
            )

    def handle_formdata(self, data: dict[str, str]) -> None:
        kind = data.get("req", "")
        if kind not in {"export", "import"}:
            self.send_bytes(b"0", "text/plain; charset=utf-8")
            return
        current_user = self.current_user()
        if not current_user:
            self.send_bytes(b"0", "text/plain; charset=utf-8", HTTPStatus.UNAUTHORIZED)
            return
        try:
            price = int(re.sub(r"[^0-9]", "", data.get("price", "")) or "0")
        except ValueError:
            price = 0
        if price <= 0:
            self.send_bytes(b"0", "text/plain; charset=utf-8")
            return
        try:
            count = int(re.sub(r"[^0-9]", "", data.get("count", "")) or "0")
        except ValueError:
            count = 0
        if kind == "export" and count <= 0:
            count = price
        with sqlite3.connect(self.db_path) as db:
            if kind == "export":
                wallet = db.execute(
                    "SELECT balance FROM wallets WHERE member_id=?",
                    (current_user,),
                ).fetchone()
                if wallet is None or int(wallet[0]) < price:
                    self.send_bytes(b"0", "text/plain; charset=utf-8")
                    return
                db.execute(
                    "UPDATE wallets SET balance=balance-? WHERE member_id=?",
                    (price, current_user),
                )
            cur = db.execute(
                """INSERT INTO transactions(
                       kind,member_id,name,bank,bankno,price,count,phone,level,status,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    kind,
                    current_user,
                    data.get("name", ""),
                    data.get("bank", ""),
                    data.get("bankno", ""),
                    price,
                    count,
                    data.get("phone", ""),
                    data.get("lev", ""),
                    "대기",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            db.commit()
            self.send_bytes(str(cur.lastrowid).encode("ascii"), "text/plain; charset=utf-8")

    def render_transactions(self, kind: str) -> bytes:
        try:
            from bs4 import BeautifulSoup
        except Exception:
            return html_page("거래 관리", "<p>관리자 화면을 불러올 수 없습니다.</p>")

        template_name = "export_list.php.html" if kind == "export" else "import_list.php.html"
        template = self.root / "adm" / template_name
        text = read_text(template)
        if kind == "export":
            text = (
                text.replace("출금", "환전")
                .replace("환전갯수", "환전건수")
                .replace("환전금액만끔", "환전 금액만큼")
                .replace("환전신청한만큰금액", "환전 신청 금액만큼")
            )
        soup = BeautifulSoup(normalize_admin_html(text), "html.parser")
        tbody = soup.select_one("#bettinglist")
        if tbody is None:
            return str(soup).encode("utf-8")
        tbody.clear()

        if kind == "import":
            title = soup.select_one("title")
            if title is not None:
                title.string = "구매 신청 | 캔디Cast"
            container_title = soup.select_one("#container_title")
            if container_title is not None:
                container_title.string = "구매 신청"

        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                """SELECT id,member_id,name,bank,bankno,price,count,phone,level,status,created_at
                   FROM transactions
                   WHERE kind=? AND COALESCE(is_deleted,0)=0 ORDER BY id DESC""",
                (kind,),
            ).fetchall()

        for row in rows:
            (
                transaction_id,
                member_id,
                name,
                bank,
                bankno,
                price,
                count,
                phone,
                level,
                status,
                created_at,
            ) = row
            common_inputs = (
                f'<input type="hidden" name="id" value="{transaction_id}">'
                f'<input type="hidden" name="kind" value="{kind}">'
            )
            if kind == "export":
                controls = (
                    '<div style="display:flex;gap:4px;justify-content:center;flex-wrap:wrap">'
                    f'<form method="post" action="{ADMIN_PREFIX}/transaction_status.php">{common_inputs}'
                    '<button class="btn btn_02" name="status" value="동결" '
                    'onclick="return confirm(\'이 환전 신청을 동결할까요?\')">동결</button></form>'
                    f'<form method="post" action="{ADMIN_PREFIX}/transaction_status.php">{common_inputs}'
                    '<button class="btn btn_02" name="status" value="취소" '
                    'onclick="return confirm(\'이 환전 신청을 취소할까요?\')">취소</button></form>'
                    f'<form method="post" action="{ADMIN_PREFIX}/transaction_status.php">{common_inputs}'
                    '<button class="btn btn_03" name="status" value="승인" '
                    'onclick="return confirm(\'이 환전 신청을 승인할까요?\')">승인</button></form></div>'
                )
                row_html = f"""<tr class="bg0 candycast-local-row">
                    <td class="td_chk"><input type="checkbox" name="chk[]" value="{transaction_id}"></td>
                    <td>{html.escape(member_id)}</td>
                    <td>{html.escape(bank or "-")}</td>
                    <td>{html.escape(bankno or "-")}</td>
                    <td>{html.escape(name or "-")}</td>
                    <td>{price:,}</td>
                    <td><font color="green">{html.escape(status)}</font></td>
                    <td>{html.escape(created_at)}</td>
                    <td>{controls}</td>
                    <td><span class="btn btn_01">신규</span></td>
                </tr>"""
            else:
                controls = (
                    '<div style="display:flex;gap:4px;justify-content:center">'
                    f'<form method="post" action="{ADMIN_PREFIX}/transaction_status.php">{common_inputs}'
                    '<button class="btn btn_03" name="status" value="완료" '
                    'onclick="return confirm(\'이 구매 신청을 완료 처리할까요?\')">완료</button></form>'
                    f'<form method="post" action="{ADMIN_PREFIX}/transaction_status.php">{common_inputs}'
                    '<button class="btn btn_02" name="status" value="취소" '
                    'onclick="return confirm(\'이 구매 신청을 취소할까요?\')">취소</button></form></div>'
                )
                row_html = f"""<tr class="bg0 candycast-local-row">
                    <td class="td_chk"><input type="checkbox" name="chk[]" value="{transaction_id}"></td>
                    <td>{html.escape(member_id)}</td>
                    <td>{price:,}원</td>
                    <td>{(count or price):,} 캔디</td>
                    <td>{html.escape(name or "-")}</td>
                    <td>{html.escape(phone or "-")}</td>
                    <td>{html.escape(level or "-")}</td>
                    <td><font color="green">{html.escape(status)}</font></td>
                    <td>{html.escape(created_at)}</td>
                    <td>{controls}</td>
                </tr>"""
            new_row = BeautifulSoup(row_html, "html.parser").find("tr")
            if new_row is not None:
                tbody.append(new_row)

        if not rows:
            empty_text = "환전 신청 내역이 없습니다." if kind == "export" else "구매 신청 내역이 없습니다."
            empty_row = BeautifulSoup(
                f'<tr><td colspan="10" style="padding:32px;text-align:center;color:#777">{empty_text}</td></tr>',
                "html.parser",
            ).find("tr")
            if empty_row is not None:
                tbody.append(empty_row)

        form = soup.select_one("#fboardlist")
        if form is not None:
            form["action"] = f"{ADMIN_PREFIX}/{'export_list.php' if kind == 'export' else 'import_list.php'}"
        count_label = soup.select_one(".ov_txt")
        if count_label is not None:
            count_label.string = "환전건수" if kind == "export" else "구매건수"
        count_badge = soup.select_one(".ov_num")
        if count_badge is not None:
            count_badge.string = f" {len(rows)}개"
        pagination = soup.select_one(".pg_wrap")
        if pagination is not None:
            pagination.decompose()
        return str(soup).encode("utf-8")

    def render_my2(
        self,
        current_user: str,
        display_name: str,
        balance: int,
        balance_status: str = "정상",
        account_status: str = "정상",
        display_grade: str = DISPLAY_GRADES[0],
        profile_image_url: str = PROFILE_FALLBACK_IMAGE,
    ) -> bytes:
        try:
            from bs4 import BeautifulSoup
        except Exception:
            return b""
        template = self.root / "my2.php.html"
        rendered = render_dynamic_page(
            template,
            logged_in=True,
            current_user=current_user,
            display_name=display_name,
            balance=balance,
            balance_status=balance_status,
            account_status=account_status,
            display_grade=display_grade,
            profile_image_url=profile_image_url,
        ).decode("utf-8")
        rendered = rendered.replace("상담원님이", f"{html.escape(display_name)}님이")
        rendered = re.sub(
            r"(환전 가능 캔디는\s*<strong>)[^<]*(</strong>)",
            rf"\g<1>{balance:,}\2",
            rendered,
        )
        soup = BeautifulSoup(rendered, "html.parser")
        with sqlite3.connect(self.db_path) as db:
            export_rows = db.execute(
                """SELECT name,bank,bankno,price,status,created_at FROM transactions
                   WHERE member_id=? AND kind='export' AND COALESCE(is_deleted,0)=0
                   ORDER BY id DESC""",
                (current_user,),
            ).fetchall()
            import_rows = db.execute(
                """SELECT price,count,status,created_at FROM transactions
                   WHERE member_id=? AND kind='import' AND COALESCE(is_deleted,0)=0
                   ORDER BY id DESC""",
                (current_user,),
            ).fetchall()

        def replace_table_rows(selector: str, values: list[list[str]], labels: list[str]) -> None:
            tbody = soup.select_one(selector)
            if tbody is None:
                return
            existing = tbody.find_all("tr", recursive=False)
            for old_row in existing[1:]:
                old_row.decompose()
            if not values:
                empty = soup.new_tag("tr", align="center")
                cell = soup.new_tag("td", colspan=str(len(labels)))
                cell["class"] = "no-txt"
                cell.string = "내용이 없습니다."
                empty.append(cell)
                tbody.append(empty)
                return
            for values_row in values:
                tr = soup.new_tag("tr", align="center")
                for label, value in zip(labels, values_row):
                    td = soup.new_tag("td")
                    td["data-label"] = label
                    td.string = value
                    tr.append(td)
                tbody.append(tr)

        replace_table_rows(
            ".my2-tab1-con table tbody",
            [
                ["캔디", created, bankno or "-", f"{price:,}", bank or "-", name or "-", status]
                for name, bank, bankno, price, status, created in export_rows
            ],
            ["상품", "환전 처리 일시", "계좌", "환전 캔디", "은행", "예금주", "상태"],
        )
        replace_table_rows(
            ".my2-tab2-con table tbody",
            [
                [created, "캔디", f"{(count or price):,}", f"{price:,}원", status]
                for price, count, status, created in import_rows
            ],
            ["구매일시", "상품", "캔디", "결제금액", "상태"],
        )
        return str(soup).encode("utf-8")

    def render_live_page(
        self,
        live_id: str,
        archive: Path,
        current_user: str,
        display_name: str,
        balance: int,
        display_grade: str = DISPLAY_GRADES[0],
        profile_image_url: str = PROFILE_FALLBACK_IMAGE,
    ) -> bytes:
        try:
            from bs4 import BeautifulSoup
        except Exception:
            return b""
        if not archive.is_file():
            archive = next(iter(sorted(self.root.glob("live__q_*.html"))), self.root / "index.html")
        rendered = render_dynamic_page(
            archive,
            logged_in=bool(current_user),
            current_user=current_user,
            display_name=display_name,
            balance=balance,
            display_grade=display_grade,
            profile_image_url=profile_image_url,
        ).decode("utf-8")
        soup = BeautifulSoup(rendered, "html.parser")
        for script in list(soup.find_all("script")):
            script_text = script.get_text(" ", strip=True)
            if "라이브 입장이 불가합니다" in script_text or "history.back()" in script_text:
                script.decompose()
        for node in list(soup.select("noscript, #validation_check")):
            if "라이브 입장이 불가합니다" in node.get_text(" ", strip=True):
                node.decompose()

        archive_map = live_id_by_archive(self.root)
        for link in soup.select('a[href*="live__q_"]'):
            match = re.search(r"live__q_([0-9a-f]{12})\.html", link.get("href", ""))
            if match and match.group(1) in archive_map:
                link["href"] = f"/live.php?live_id={archive_map[match.group(1)]}"

        index_soup = BeautifulSoup(read_text(self.root / "index.html"), "html.parser")
        matching_links = index_soup.select(f'a[href="/live.php?live_id={live_id}"]')
        selected = max(
            matching_links,
            key=lambda link: 1 if link.select_one('img[src*="thumb-"]') else 0,
            default=None,
        )
        poster = "/img/no_profile.gif"
        title = f"라이브 채널 {live_id}"
        broadcaster = "CandyCast"
        if selected is not None:
            poster_image = selected.select_one('img[src*="thumb-"]') or selected.find("img")
            if poster_image is not None:
                poster = poster_image.get("src", poster)
            metadata_scope = selected if selected.select_one(".con") else selected.find_parent("li") or selected
            title_node = metadata_scope.select_one(".con strong")
            broadcaster_node = metadata_scope.select_one(".con p") or metadata_scope.select_one(".con > div > span")
            if title_node is not None:
                title = title_node.get_text(" ", strip=True)
            else:
                title = selected.get_text(" ", strip=True).replace("Live", "").strip() or title
            if broadcaster_node is not None:
                broadcaster = broadcaster_node.get_text(" ", strip=True)
            elif title:
                broadcaster = title

        with sqlite3.connect(self.db_path) as db:
            messages = db.execute(
                """SELECT sender_id,message,created_at FROM chat_messages
                   WHERE receiver_id=? ORDER BY id DESC LIMIT 50""",
                (f"live:{live_id}",),
            ).fetchall()[::-1]
        message_html = "".join(
            f'<li><strong>{html.escape(sender)}</strong><span>{html.escape(message)}</span><time>{html.escape(created[11:16])}</time></li>'
            for sender, message, created in messages
        ) or '<li class="empty">아직 등록된 채팅이 없습니다.</li>'
        composer = (
            f'<form method="post" action="/chat/live_message?live_id={quote(live_id)}">'
            '<input name="message" maxlength="500" required aria-label="채팅 메시지">'
            '<button type="submit">전송</button></form>'
            if current_user
            else '<a class="live-login" href="/bbs/login.php">로그인 후 채팅하기</a>'
        )
        viewer_html = f"""<div id="contents" class="candycast-live-detail">
            <div class="candycast-live-layout">
                <section class="candycast-player">
                    <div class="candycast-poster"><img src="{html.escape(poster, quote=True)}" alt="{html.escape(title, quote=True)}"></div>
                    <div class="candycast-live-info"><span class="live-badge">LIVE</span><div><h1>{html.escape(title)}</h1><p>{html.escape(broadcaster)}</p></div></div>
                </section>
                <aside class="candycast-live-chat"><h2>실시간 채팅</h2><ul>{message_html}</ul>{composer}</aside>
            </div>
        </div>"""
        container = soup.select_one("#container") or soup.body
        if container is not None:
            viewer = BeautifulSoup(viewer_html, "html.parser").find(id="contents")
            gnb = container.select_one("#gnb")
            if viewer is not None:
                if gnb is not None:
                    gnb.insert_after(viewer)
                else:
                    container.append(viewer)
        if soup.head is not None:
            style = soup.new_tag("style")
            style.string = """
.candycast-live-detail{padding:24px 28px 50px;box-sizing:border-box}.candycast-live-layout{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:18px;max-width:1500px;margin:0 auto}.candycast-player{min-width:0}.candycast-poster{aspect-ratio:16/9;background:#111;overflow:hidden}.candycast-poster img{width:100%;height:100%;object-fit:contain;display:block}.candycast-live-info{display:flex;align-items:center;gap:12px;padding:18px 4px}.candycast-live-info h1{font-size:22px;line-height:1.35;margin:0 0 4px}.candycast-live-info p{margin:0;color:#777}.live-badge{flex:0 0 auto;border:1px solid #ff245f;color:#ff245f;font-size:12px;padding:3px 7px;border-radius:4px}.candycast-live-chat{border:1px solid #ddd;background:#fff;display:grid;grid-template-rows:auto 1fr auto;min-height:420px}.candycast-live-chat h2{font-size:16px;margin:0;padding:14px;border-bottom:1px solid #eee}.candycast-live-chat ul{list-style:none;margin:0;padding:12px;overflow:auto;max-height:620px}.candycast-live-chat li{display:grid;grid-template-columns:auto 1fr auto;gap:7px;padding:7px 0;font-size:13px}.candycast-live-chat li span{overflow-wrap:anywhere}.candycast-live-chat time{color:#aaa}.candycast-live-chat .empty{display:block;color:#888;text-align:center;padding-top:40px}.candycast-live-chat form{display:flex;border-top:1px solid #eee;padding:10px}.candycast-live-chat input{min-width:0;flex:1;border:1px solid #ccc;padding:9px}.candycast-live-chat button{border:0;background:#f44776;color:#fff;padding:0 14px}.live-login{margin:10px;background:#333;color:#fff!important;text-align:center;padding:10px}@media(max-width:900px){.candycast-live-detail{padding:16px}.candycast-live-layout{grid-template-columns:1fr}.candycast-live-chat{min-height:340px}.candycast-live-info h1{font-size:18px}}
"""
            soup.head.append(style)
        return str(soup).encode("utf-8")

    def render_chat(self, receiver: str, current_user: str) -> bytes:
        member_state = self.member_state(current_user)
        viewed_at = now_text()
        with self.support_db() as db:
            self.touch_member_chat_room(db, current_user, receiver, viewed_at)
            db.execute(
                """UPDATE chat_messages SET read_at=?
                   WHERE member_id=? AND influencer_id=?
                     AND sender_id=? AND receiver_id=? AND read_at=''""",
                (viewed_at, current_user, receiver, receiver, current_user),
            )
            rows = db.execute(
                """SELECT id,sender_id,receiver_id,message,
                          attachment_name,attachment_type,attachment_data,
                          created_at,read_at,edited_at,deleted_by_member
                   FROM chat_messages
                   WHERE member_id=? AND influencer_id=?
                   ORDER BY id ASC LIMIT 500""",
                (current_user, receiver),
            ).fetchall()
            db.commit()
        profile = self.member_chat_profile(receiver)
        room_name = profile["name"]
        room_subtitle = profile["nickname"]
        profile_image = profile["image"]
        bubble_items = []
        for row in rows:
            sender = str(row["sender_id"])
            message = str(row["message"] or "")
            deleted_by_member = bool(row["deleted_by_member"])
            attachment_html = ""
            if row["attachment_data"] and not deleted_by_member:
                attachment_html = (
                    '<img class="cc-chat-message-image" '
                    f'src="{html.escape(str(row["attachment_data"]), quote=True)}" '
                    f'alt="{html.escape(str(row["attachment_name"] or "첨부 이미지"), quote=True)}">'
                )
            if deleted_by_member:
                message_html = '<p class="cc-chat-deleted">삭제된 메시지입니다.</p>'
            else:
                message_html = f"<p>{html.escape(message)}</p>" if message else ""
            edited_html = (
                '<small class="cc-chat-edited">수정됨</small>'
                if row["edited_at"] and not deleted_by_member
                else ""
            )
            actions_html = (
                '<button type="button" class="cc-chat-message-more" '
                'data-cc-chat-action="message-menu" aria-label="메시지 옵션">&#8942;</button>'
                '<span class="cc-chat-message-actions" hidden>'
                f'<button type="button" data-cc-chat-action="delete-message" data-message-id="{int(row["id"])}">메시지 삭제</button>'
                '</span>'
                if sender == current_user and not deleted_by_member
                else ""
            )
            bubble_items.append(
                f'<li class="{"mine" if sender == current_user else "theirs"}" data-message-id="{int(row["id"])}"><div>'
                f'<strong>{"나" if sender == current_user else html.escape(room_name)}</strong>'
                f'{message_html}{attachment_html}'
                f'{actions_html}<time>{html.escape(str(row["created_at"]))}{edited_html}</time></div></li>'
            )
        bubbles = "".join(bubble_items) or '<li class="empty">첫 메시지를 보내 대화를 시작해보세요.</li>'
        page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>{html.escape(room_name)} | CandyCast 채팅</title><style>
*{{box-sizing:border-box;letter-spacing:0}}html,body{{height:100%}}body{{margin:0;font-family:Arial,'Malgun Gothic',sans-serif;background:#eef0f4;color:#222}}.cc-chat-header{{height:64px;max-width:720px;margin:0 auto;background:#fff;display:grid;grid-template-columns:44px 44px minmax(0,1fr) 44px;align-items:center;gap:10px;padding:7px 14px;border-bottom:1px solid #dedfe4}}.cc-chat-back,.cc-chat-close{{display:flex;width:44px;height:44px;align-items:center;justify-content:center;color:#222!important;line-height:1;text-decoration:none!important}}.cc-chat-back{{font-size:36px}}.cc-chat-close{{border-radius:50%;font-size:29px}}.cc-chat-back:hover,.cc-chat-close:hover{{background:#f2f3f5}}.cc-chat-back:focus-visible,.cc-chat-close:focus-visible{{outline:2px solid #ef4778;outline-offset:-2px}}.cc-chat-avatar{{width:44px;height:44px;border-radius:50%;object-fit:cover;background:#f0f1f4}}.cc-chat-heading{{min-width:0;display:flex;flex-direction:column}}.cc-chat-heading strong,.cc-chat-heading span{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.cc-chat-heading strong{{font-size:16px}}.cc-chat-heading span{{margin-top:2px;color:#7b7e86;font-size:12px}}main{{height:calc(100vh - 64px);height:calc(100dvh - 64px);display:grid;grid-template-rows:minmax(0,1fr) auto;max-width:720px;margin:0 auto;background:#fff}}.cc-chat-messages{{list-style:none;margin:0;padding:18px;overflow:auto;overscroll-behavior:contain;background:#f7f8fa}}.cc-chat-messages li{{display:flex;margin:0 0 12px}}.cc-chat-messages li.mine{{justify-content:flex-end}}.cc-chat-messages li>div{{position:relative;max-width:78%;background:#fff;padding:10px 12px;border:1px solid #e1e2e6;border-radius:4px 12px 12px 12px;box-shadow:0 1px 2px rgba(25,28,38,.06)}}.cc-chat-messages li.mine>div{{background:#ffe2eb;border-color:#ffd2df;border-radius:12px 4px 12px 12px}}.cc-chat-messages li strong{{display:block;font-size:12px}}.cc-chat-messages li p{{margin:4px 0;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere}}.cc-chat-messages li time{{display:block;color:#8a8d94;font-size:11px;text-align:right}}.cc-chat-messages li.empty{{justify-content:center;color:#888;padding-top:80px}}.cc-chat-composer{{border-top:1px solid #dedfe4;padding:9px 12px;background:#fff}}.cc-chat-composer-row{{display:flex;align-items:flex-end;gap:8px}}.cc-chat-composer textarea{{min-width:0;flex:1;resize:none;height:48px;max-height:112px;border:1px solid #c7c9cf;border-radius:4px;padding:9px 11px;font:inherit;line-height:1.4}}.cc-chat-attach,.cc-chat-send{{min-height:44px;border:0;border-radius:4px;cursor:pointer}}.cc-chat-attach{{width:44px;background:#eef0f4;color:#4d5360;font-size:21px}}.cc-chat-send{{width:74px;background:#ef4778;color:#fff;font-weight:700}}.cc-chat-attach:disabled,.cc-chat-send:disabled{{cursor:wait;opacity:.55}}.cc-chat-attachment-preview{{display:flex;align-items:center;gap:8px;margin-bottom:8px;padding:6px 8px;border:1px solid #e1e2e6;border-radius:4px;background:#f7f8fa}}.cc-chat-attachment-preview[hidden]{{display:none!important}}.cc-chat-attachment-preview img{{width:44px;height:44px;border-radius:4px;object-fit:cover}}.cc-chat-attachment-preview span{{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:#666}}.cc-chat-attachment-preview button{{width:36px;height:36px;border:0;background:transparent;font-size:22px;cursor:pointer}}.cc-chat-message-image{{display:block;max-width:min(100%,360px);max-height:360px;margin-top:7px;border-radius:6px;object-fit:contain;background:#eceef2}}.cc-chat-message-more{{width:30px;height:30px;margin:4px -7px -3px 4px;border:0;border-radius:50%;background:transparent;color:#757983;font-size:20px;line-height:1;cursor:pointer;float:right}}.cc-chat-message-more:hover,.cc-chat-message-more:focus-visible{{background:rgba(255,255,255,.72)}}.cc-chat-message-actions{{position:absolute;z-index:3;right:8px;bottom:31px;padding:4px;border:1px solid #d8dae0;border-radius:5px;background:#fff;box-shadow:0 4px 14px rgba(26,29,38,.15)}}.cc-chat-message-actions[hidden]{{display:none!important}}.cc-chat-message-actions button{{min-height:36px;padding:0 12px;border:0;background:#fff;color:#c92e50;font-weight:700;cursor:pointer;white-space:nowrap}}.cc-chat-deleted{{color:#8b8e96;font-style:italic}}.cc-chat-edited{{margin-left:5px;color:#a0a3aa;font-size:10px;font-weight:400}}
.cc-chat-message-more{{position:absolute;right:0;bottom:-5px;width:44px;height:44px;margin:0;float:none}}
.cc-chat-messages li.mine time{{padding-right:28px}}
.cc-chat-message-actions{{bottom:38px}}
</style></head><body class="cc-public-page cc-page-chat cc-mobile-immersive" data-cc-authenticated="1" data-cc-user="{html.escape(current_user, quote=True)}" data-balance-status="{html.escape(str(member_state['balance_status']), quote=True)}" data-account-status="{html.escape(str(member_state['account_status']), quote=True)}"><header class="cc-chat-header"><a class="cc-chat-back" href="/chatlist.php" aria-label="채팅 목록으로 돌아가기">&#8249;</a><img class="cc-chat-avatar" src="{html.escape(profile_image, quote=True)}" alt=""><div class="cc-chat-heading"><strong>{html.escape(room_name)}</strong><span>{html.escape(room_subtitle)}</span></div><a class="cc-chat-close" href="/chatlist.php" aria-label="인플루언서 채팅 닫기">&times;</a></header><main><ul class="cc-chat-messages" aria-live="polite">{bubbles}</ul><form class="cc-chat-composer" method="post" action="/chat/memo_form.php?me_recv_mb_id={quote(receiver)}" data-influencer-id="{html.escape(receiver, quote=True)}"><div class="cc-chat-attachment-preview" hidden><img alt="첨부 이미지 미리보기"><span></span><button type="button" data-cc-chat-action="remove-attachment" aria-label="첨부 이미지 삭제">&times;</button></div><div class="cc-chat-composer-row"><input id="cc-member-chat-file" type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden><button type="button" class="cc-chat-attach" data-cc-chat-action="attach" aria-label="사진 첨부">&#128206;</button><textarea name="message" maxlength="1000" placeholder="메시지를 입력하세요" aria-label="메시지"></textarea><button type="submit" class="cc-chat-send">전송</button></div></form></main></body></html>"""
        page = page.replace(
            "</head>",
            '<link rel="stylesheet" href="/assets/local/candycast-support.css?v=20260717-chat3">'
            '<link rel="stylesheet" href="/assets/local/candycast-member-chat.css?v=20260717-chat3">'
            '<link rel="stylesheet" href="/assets/local/candycast-mobile.css?v=20260718-ranking2">'
            '<link rel="stylesheet" href="/assets/local/candycast-restrictions.css"></head>',
        ).replace(
            "</body>",
            support_widget_markup(True, current_user, self.display_name(current_user))
            + member_chat_widget_markup()
            + mobile_navigation_markup(True)
            + '<script src="/assets/local/candycast-image-utils.js" defer></script>'
            + '<script src="/assets/local/candycast-support.js?v=20260717-chat3" defer></script>'
            + '<script src="/assets/local/candycast-member-chat.js?v=20260717-chat3" defer></script>'
            + '<script src="/assets/local/candycast-mobile.js?v=20260717-audit1" defer></script>'
            + '<script src="/assets/local/candycast-restrictions.js" defer></script></body>',
        )
        return page.encode("utf-8")

    def render_maintenance(self, filename: str, ran: bool = False) -> bytes:
        try:
            from bs4 import BeautifulSoup
        except Exception:
            return b""
        soup = BeautifulSoup(normalize_admin_html(read_text(self.root / "adm" / "index.html")), "html.parser")
        container = soup.select_one("#container") or soup.body
        if container is None:
            return str(soup).encode("utf-8")
        container.clear()
        buttons = []
        for action_file, (label, warning) in MAINTENANCE_ACTIONS.items():
            active = " active" if action_file == filename else ""
            buttons.append(
                f'<a class="maintenance-button{active}" href="{ADMIN_PREFIX}/{action_file}?run=1" '
                f'onclick="return confirm({html.escape(json.dumps(warning, ensure_ascii=False), quote=True)})">{label}</a>'
            )
        label = MAINTENANCE_ACTIONS[filename][0]
        result = (
            '<div class="maintenance-result">로컬 서버의 임시 파일을 확인했습니다. 삭제할 파일이 없어 완료되었습니다.</div>'
            if ran
            else ""
        )
        fragment = BeautifulSoup(
            f"""<div class="maintenance-page"><h1>{label}</h1><div class="maintenance-buttons">{''.join(buttons)}</div>{result}<p>관리자 유지보수 작업입니다.</p></div>""",
            "html.parser",
        )
        container.append(fragment)
        if soup.head is not None:
            style = soup.new_tag("style")
            style.string = ".maintenance-page{padding:28px}.maintenance-page h1{font-size:24px;margin:0 0 24px}.maintenance-buttons{display:flex;flex-wrap:wrap;gap:8px}.maintenance-button{display:inline-flex;align-items:center;min-height:38px;padding:0 14px;border:1px solid #aaa;background:#fff;color:#222!important}.maintenance-button.active{background:#344fc4;color:#fff!important;border-color:#344fc4}.maintenance-result{margin-top:20px;padding:14px;border:1px solid #b8dfc8;background:#f1fff6;color:#197548}.maintenance-page p{margin-top:18px;color:#777}"
            soup.head.append(style)
        return str(soup).encode("utf-8")

    def render_imported_logs(self) -> bytes:
        try:
            from bs4 import BeautifulSoup
        except Exception:
            return b""
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT source_file,row_json FROM imported_logs ORDER BY id DESC LIMIT 500").fetchall()
        trs = ""
        for source, row_json in rows:
            try:
                row = json.loads(row_json)
            except json.JSONDecodeError:
                row = [row_json]
            trs += "<tr><td>{}</td><td>{}</td></tr>".format(
                html.escape(source),
                html.escape(" | ".join(str(x) for x in row)),
            )
        soup = BeautifulSoup(
            normalize_admin_html(read_text(self.root / "adm" / "index.html")),
            "html.parser",
        )
        container = soup.select_one("#container") or soup.body
        if container is None:
            return str(soup).encode("utf-8")
        container.clear()
        fragment = BeautifulSoup(
            f"""<div style="padding:28px"><h1 style="font-size:24px;margin:0 0 18px">가져온 관리자 로그</h1>
            <p class="local_desc01 local_desc">백업 CSV에서 가져온 최근 500행입니다.</p>
            <div class="tbl_head01 tbl_wrap"><table><thead><tr><th>파일</th><th>내용</th></tr></thead><tbody>{trs}</tbody></table></div></div>""",
            "html.parser",
        )
        container.append(fragment)
        return str(soup).encode("utf-8")

    def serve_static(
        self,
        path: str,
        logged_in: bool = False,
        login_error: bool = False,
        current_user: str = "",
        display_name: str = "",
        balance: int = 0,
        balance_status: str = "정상",
        account_status: str = "정상",
        display_grade: str = DISPLAY_GRADES[0],
        profile_image_url: str = PROFILE_FALLBACK_IMAGE,
        notice: str = "",
        login_target: str = "/",
        cookies: list[str] | None = None,
    ) -> None:
        parsed = urlparse(self.path)
        request_path = unquote(path or parsed.path)
        archive_request_path = archived_admin_path(request_path)
        rel = archive_request_path.lstrip("/") or "index.html"
        root = self.root.resolve()
        candidates: list[Path] = []

        base_candidate = self.root / rel
        candidates.append(base_candidate)
        if base_candidate.is_dir():
            candidates.append(base_candidate / "index.html")

        rel_path = Path(rel)
        suffix = rel_path.suffix.lower()
        if parsed.query:
            digest = query_hash(parsed.query)
            stem_rel = rel[: -len(suffix)] if suffix else rel.rstrip("/")
            if stem_rel:
                candidates.append(self.root / f"{stem_rel}__q_{digest}.html")
        if suffix in {".php", ".htm"}:
            candidates.append(self.root / f"{rel}.html")
        elif not suffix and rel != "index.html":
            candidates.append(self.root / rel / "index.html")
            candidates.append(self.root / f"{rel}.html")

        candidate = None
        for item in candidates:
            resolved = item.resolve()
            if str(resolved).startswith(str(root)) and resolved.is_file():
                candidate = resolved
                break
        if candidate is None:
            if is_admin_path(request_path):
                self.send_redirect(f"{ADMIN_PREFIX}/members")
                return
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = candidate.read_bytes()
        ctype = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if candidate.suffix.lower() in {".html", ".php"}:
            ctype = "text/html; charset=utf-8"
            if is_admin_path(request_path):
                data = normalize_admin_html(data.decode("utf-8", errors="ignore")).encode("utf-8")
            else:
                data = render_dynamic_page(
                    candidate,
                    logged_in=logged_in,
                    login_error=login_error,
                    current_user=current_user,
                    display_name=display_name,
                    balance=balance,
                    balance_status=balance_status,
                    account_status=account_status,
                    display_grade=display_grade,
                    profile_image_url=profile_image_url,
                    notice=notice,
                    login_target=login_target,
                )
        elif candidate.suffix.lower() == ".css":
            ctype = "text/css; charset=utf-8"
        elif candidate.suffix.lower() == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif candidate.name in {"logo_img", "logo_img2"}:
            ctype = "image/png"
        self.send_bytes(data, ctype, cookies=cookies)

    def log_message(self, *_: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="Detached clone directory")
    parser.add_argument("--backup", default="", help="Original backup directory with exports/tables")
    parser.add_argument("--workdir", default="pulseutv_standalone_runtime")
    parser.add_argument("--site-dir", default="", help="Prepared static site directory")
    parser.add_argument("--db-path", default="", help="SQLite database path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--no-prepare", action="store_true")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    workdir = Path(args.workdir).resolve()
    site_dir = Path(args.site_dir).resolve() if args.site_dir else workdir / "site"
    db_path = Path(args.db_path).resolve() if args.db_path else workdir / "candycast.sqlite3"
    backup_dir = Path(args.backup).resolve() if args.backup else None
    configure_admin_auth(workdir)
    if not args.no_prepare:
        prepare_site(source, site_dir)
        normalize_admin_brand_tree(site_dir / "adm")
    ensure_local_assets(site_dir)
    init_db(db_path, backup_dir, site_dir)
    refresh_banned_ips(db_path)
    if args.prepare_only:
        print(site_dir)
        return 0
    StandaloneHandler.root = site_dir
    StandaloneHandler.db_path = db_path
    server = CandyCastHTTPServer((args.host, args.port), StandaloneHandler)
    print(f"Serving CandyCast at http://{args.host}:{args.port}/ from {site_dir}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
