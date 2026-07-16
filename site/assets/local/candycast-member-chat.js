(function () {
  "use strict";

  var root = document.getElementById("cc-member-chat-root");
  if (!root) return;

  var launcher = root.querySelector(".cc-member-chat-launcher");
  var panel = root.querySelector(".cc-member-chat-panel");
  var list = root.querySelector(".cc-member-chat-list");
  var badge = root.querySelector(".cc-member-chat-unread");
  var pollTimer = 0;
  var loading = false;

  function closeSupport() {
    var support = document.getElementById("cc-support-root");
    if (!support || support.dataset.open !== "true") return;
    var toggle = support.querySelector('[data-cc-support-action="toggle"]');
    if (toggle) toggle.click();
  }

  function setOpen(open) {
    root.dataset.open = open ? "true" : "false";
    launcher.setAttribute("aria-expanded", open ? "true" : "false");
    launcher.setAttribute("aria-label", open ? "개인 채팅 목록 닫기" : "개인 채팅 목록 열기");
    panel.setAttribute("aria-hidden", open ? "false" : "true");
    document.body.classList.toggle("cc-member-chat-open", open);
    if (open) closeSupport();
  }

  function capCount(value) {
    return value > 99 ? "99+" : String(value);
  }

  function setBadge(unread) {
    var count = Math.max(0, Number(unread) || 0);
    badge.textContent = capCount(count);
    badge.hidden = count === 0;
  }

  function formatTime(value) {
    if (!value) return "";
    var parsed = new Date(String(value).replace(" ", "T"));
    if (Number.isNaN(parsed.getTime())) return "";
    var now = new Date();
    if (parsed.toDateString() === now.toDateString()) {
      return String(parsed.getHours()).padStart(2, "0") + ":" +
        String(parsed.getMinutes()).padStart(2, "0");
    }
    return (parsed.getMonth() + 1) + "." + parsed.getDate() + ".";
  }

  function makeText(tag, className, value) {
    var node = document.createElement(tag);
    node.className = className;
    node.textContent = value || "";
    return node;
  }

  function makeRoom(room) {
    var link = document.createElement("a");
    link.className = "cc-member-chat-row";
    link.href = room.href || "/chatlist.php";
    link.setAttribute("role", "listitem");

    var avatar = document.createElement("img");
    avatar.className = "cc-member-chat-avatar";
    avatar.src = room.image || "/img/no_profile.gif";
    avatar.alt = "";
    avatar.loading = "lazy";
    avatar.decoding = "async";
    avatar.addEventListener("error", function () {
      if (!avatar.src.endsWith("/img/no_profile.gif")) {
        avatar.src = "/img/no_profile.gif";
      }
    });

    var copy = document.createElement("span");
    copy.className = "cc-member-chat-copy";
    copy.appendChild(makeText("strong", "cc-member-chat-name", room.name || room.nickname || room.id));
    copy.appendChild(makeText("span", "cc-member-chat-preview", room.lastMessage || "대화를 시작해보세요."));

    var meta = document.createElement("span");
    meta.className = "cc-member-chat-meta";
    meta.appendChild(makeText("time", "cc-member-chat-time", formatTime(room.updatedAt)));
    var unread = Math.max(0, Number(room.unread) || 0);
    if (unread) meta.appendChild(makeText("i", "cc-member-chat-count", capCount(unread)));

    link.appendChild(avatar);
    link.appendChild(copy);
    link.appendChild(meta);
    return link;
  }

  function render(payload) {
    var rooms = Array.isArray(payload.rooms) ? payload.rooms : [];
    list.replaceChildren();
    rooms.forEach(function (room) {
      list.appendChild(makeRoom(room));
    });
    if (!rooms.length) {
      list.appendChild(makeText("p", "cc-member-chat-empty", "아직 저장된 개인 채팅이 없습니다."));
    }
    root.hidden = rooms.length === 0;
    document.body.classList.toggle("cc-member-chat-available", rooms.length > 0);
    if (!rooms.length) setOpen(false);
    setBadge(payload.unread);
  }

  async function loadRooms() {
    if (loading || document.hidden) return;
    loading = true;
    try {
      var response = await fetch("/api/member/chats", {
        credentials: "same-origin",
        headers: { Accept: "application/json" }
      });
      if (response.status === 401) {
        root.hidden = true;
        document.body.classList.remove("cc-member-chat-available");
        return;
      }
      if (!response.ok) throw new Error("member chat request failed");
      render(await response.json());
    } catch (_error) {
      if (!root.dataset.loaded) root.hidden = true;
    } finally {
      root.dataset.loaded = "true";
      loading = false;
    }
  }

  root.addEventListener("click", function (event) {
    var action = event.target.closest("[data-cc-member-chat-action]");
    if (!action) return;
    var name = action.getAttribute("data-cc-member-chat-action");
    if (name === "toggle") setOpen(root.dataset.open !== "true");
    if (name === "close") setOpen(false);
  });

  document.addEventListener("click", function (event) {
    if (root.dataset.open === "true" && !root.contains(event.target)) setOpen(false);
    if (
      root.dataset.open === "true" &&
      event.target.closest("#cc-support-root [data-cc-support-action]")
    ) {
      setOpen(false);
    }
  }, true);

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && root.dataset.open === "true") {
      setOpen(false);
      launcher.focus();
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) loadRooms();
  });

  var messages = document.querySelector(".cc-chat-messages");
  if (messages) messages.scrollTop = messages.scrollHeight;

  loadRooms();
  pollTimer = window.setInterval(loadRooms, 10000);
  window.addEventListener("pagehide", function () {
    window.clearInterval(pollTimer);
  }, { once: true });
}());
