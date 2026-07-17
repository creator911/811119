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

  function initializeDetailComposer() {
    var form = document.querySelector(".cc-chat-composer[data-influencer-id]");
    if (!form) return;
    var textarea = form.querySelector("textarea[name=message]");
    var fileInput = form.querySelector("#cc-member-chat-file");
    var attachButton = form.querySelector(".cc-chat-attach");
    var sendButton = form.querySelector(".cc-chat-send");
    var preview = form.querySelector(".cc-chat-attachment-preview");
    var messageList = document.querySelector(".cc-chat-messages");
    var pendingAttachment = null;
    var attachmentBusy = false;
    var attachmentVersion = 0;
    var sending = false;

    function setBusy() {
      if (attachButton) attachButton.disabled = attachmentBusy || sending;
      if (sendButton) sendButton.disabled = attachmentBusy || sending;
    }

    function clearAttachment() {
      attachmentVersion += 1;
      pendingAttachment = null;
      attachmentBusy = false;
      if (fileInput) fileInput.value = "";
      if (preview) {
        preview.hidden = true;
        preview.querySelector("img").removeAttribute("src");
        preview.querySelector("span").textContent = "";
      }
      setBusy();
    }

    if (attachButton && fileInput) {
      attachButton.addEventListener("click", function () { fileInput.click(); });
    }
    form.addEventListener("click", function (event) {
      if (event.target.closest('[data-cc-chat-action="remove-attachment"]')) {
        clearAttachment();
      }
    });

    if (fileInput) {
      fileInput.addEventListener("change", function () {
        var file = fileInput.files && fileInput.files[0];
        if (!file) return clearAttachment();
        if (!window.CandyCastImage) {
          window.alert("이미지 압축기를 불러오지 못했습니다. 페이지를 새로고침해 주세요.");
          return clearAttachment();
        }
        var version = attachmentVersion + 1;
        attachmentVersion = version;
        attachmentBusy = true;
        setBusy();
        preview.hidden = false;
        preview.querySelector("img").removeAttribute("src");
        preview.querySelector("span").textContent = "사진 자동 압축 중...";
        window.CandyCastImage.compress(file).then(function (result) {
          if (version !== attachmentVersion) return;
          pendingAttachment = { name: result.name, type: result.type, data: result.data };
          preview.querySelector("img").src = pendingAttachment.data;
          preview.querySelector("span").textContent = result.label;
          preview.hidden = false;
        }).catch(function (error) {
          if (version !== attachmentVersion) return;
          window.alert(error.message);
          clearAttachment();
        }).finally(function () {
          if (version === attachmentVersion) {
            attachmentBusy = false;
            setBusy();
          }
        });
      });
    }

    if (textarea) {
      textarea.addEventListener("input", function () {
        textarea.style.height = "48px";
        textarea.style.height = Math.min(112, textarea.scrollHeight) + "px";
      });
      textarea.addEventListener("keydown", function (event) {
        if (
          event.key === "Enter" && !event.shiftKey &&
          !event.isComposing && event.keyCode !== 229
        ) {
          event.preventDefault();
          if (form.requestSubmit) form.requestSubmit();
        }
      });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (sending) return;
      if (attachmentBusy) {
        window.alert("사진 압축이 끝날 때까지 잠시 기다려 주세요.");
        return;
      }
      var message = (textarea.value || "").trim();
      if (!message && !pendingAttachment) return;
      sending = true;
      setBusy();
      fetch("/api/member/chat/messages", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          influencerId: form.dataset.influencerId,
          message: message,
          attachment: pendingAttachment
        })
      }).then(async function (response) {
        var payload = {};
        try { payload = await response.json(); } catch (_error) { payload = {}; }
        if (!response.ok) throw new Error(payload.error || "메시지를 전송하지 못했습니다.");
        if (textarea) textarea.value = "";
        clearAttachment();
        window.location.reload();
      }).catch(function (error) {
        window.alert(error.message);
      }).finally(function () {
        sending = false;
        setBusy();
      });
    });

    if (messageList) {
      messageList.addEventListener("click", function (event) {
        var action = event.target.closest("[data-cc-chat-action]");
        if (!action) return;
        var name = action.dataset.ccChatAction;
        if (name === "message-menu") {
          var menu = action.nextElementSibling;
          messageList.querySelectorAll(".cc-chat-message-actions").forEach(function (item) {
            if (item !== menu) item.hidden = true;
          });
          if (menu) menu.hidden = !menu.hidden;
          return;
        }
        if (name !== "delete-message") return;
        if (!window.confirm("이 메시지를 삭제할까요?")) return;
        action.disabled = true;
        fetch("/api/member/chat/messages/delete", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({
            id: action.dataset.messageId,
            influencerId: form.dataset.influencerId
          })
        }).then(async function (response) {
          var payload = {};
          try { payload = await response.json(); } catch (_error) { payload = {}; }
          if (!response.ok) throw new Error(payload.error || "메시지를 삭제하지 못했습니다.");
          window.location.reload();
        }).catch(function (error) {
          window.alert(error.message);
          action.disabled = false;
        });
      });
    }
  }

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

  initializeDetailComposer();

  loadRooms();
  pollTimer = window.setInterval(loadRooms, 10000);
  window.addEventListener("pagehide", function () {
    window.clearInterval(pollTimer);
  }, { once: true });
}());
