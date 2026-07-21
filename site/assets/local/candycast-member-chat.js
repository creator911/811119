(function () {
  "use strict";

  var root = document.getElementById("cc-member-chat-root");
  if (!root) return;

  var launcher = root.querySelector(".cc-member-chat-launcher");
  var panel = root.querySelector(".cc-member-chat-panel");
  var listView = root.querySelector(".cc-member-chat-list-view");
  var detailView = root.querySelector(".cc-member-chat-detail");
  var list = root.querySelector(".cc-member-chat-list");
  var badge = root.querySelector(".cc-member-chat-unread");
  var detailMessages = root.querySelector(".cc-member-chat-messages");
  var detailForm = root.querySelector(".cc-member-chat-composer");
  var detailTextarea = detailForm.querySelector("textarea[name=message]");
  var detailFile = detailForm.querySelector(".cc-member-chat-file");
  var detailAttach = detailForm.querySelector(".cc-member-chat-attach");
  var detailSend = detailForm.querySelector(".cc-member-chat-send");
  var detailPreview = detailForm.querySelector(".cc-member-chat-attachment-preview");
  var pollTimer = 0;
  var loading = false;
  var detailLoading = false;
  var detailSending = false;
  var detailAttachmentBusy = false;
  var detailAttachmentVersion = 0;
  var detailAttachment = null;
  var activeRoom = null;
  var roomCache = new Map();
  var detailSignature = "";

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
    else showRoomList();
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

  function messageTime(value) {
    var match = String(value || "").match(/(\d{2}):(\d{2})/);
    return match ? match[1] + ":" + match[2] : "";
  }

  function setDetailBusy() {
    detailAttach.disabled = detailAttachmentBusy || detailSending;
    detailSend.disabled = detailAttachmentBusy || detailSending;
  }

  function clearDetailAttachment() {
    detailAttachmentVersion += 1;
    detailAttachment = null;
    detailAttachmentBusy = false;
    detailFile.value = "";
    detailPreview.hidden = true;
    detailPreview.querySelector("img").removeAttribute("src");
    detailPreview.querySelector("span").textContent = "";
    setDetailBusy();
  }

  function showRoomList() {
    activeRoom = null;
    detailSignature = "";
    clearDetailAttachment();
    detailTextarea.value = "";
    detailTextarea.style.height = "48px";
    listView.hidden = false;
    detailView.hidden = true;
    root.dataset.view = "list";
  }

  function setDetailHeader(profile) {
    var avatar = detailView.querySelector(".cc-member-chat-detail-avatar");
    avatar.src = profile.image || "/img/no_profile.gif";
    avatar.alt = (profile.name || profile.nickname || "BJ") + " 프로필";
    detailView.querySelector(".cc-member-chat-detail-head strong").textContent =
      profile.name || profile.nickname || profile.id;
    detailView.querySelector(".cc-member-chat-detail-head span").textContent =
      profile.nickname || profile.name || profile.id;
  }

  function makeDetailMessage(item, profile) {
    var mine = item.sender === "member";
    var row = document.createElement("li");
    row.className = mine ? "mine" : "theirs";
    row.dataset.messageId = item.id;
    var bubble = document.createElement("div");
    bubble.className = "cc-member-chat-message-bubble";
    bubble.appendChild(makeText(
      "strong",
      "",
      mine ? "나" : (item.senderLabel || profile.name || profile.nickname),
    ));
    if (item.deletedByMember) {
      bubble.appendChild(makeText("p", "cc-member-chat-deleted", "삭제된 메시지입니다."));
    } else if (item.message) {
      bubble.appendChild(makeText("p", "", item.message));
    }
    if (!item.deletedByMember && item.attachment && item.attachment.data) {
      var image = document.createElement("img");
      image.className = "cc-member-chat-message-image";
      image.src = item.attachment.data;
      image.alt = item.attachment.name || "첨부 이미지";
      bubble.appendChild(image);
    }
    if (mine && !item.deletedByMember) {
      var more = document.createElement("button");
      more.type = "button";
      more.className = "cc-member-chat-message-more";
      more.dataset.ccMemberChatAction = "message-menu";
      more.setAttribute("aria-label", "메시지 옵션");
      more.textContent = "⋮";
      var actions = document.createElement("span");
      actions.className = "cc-member-chat-message-actions";
      actions.hidden = true;
      var remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.ccMemberChatAction = "delete-message";
      remove.dataset.messageId = item.id;
      remove.textContent = "메시지 삭제";
      actions.appendChild(remove);
      bubble.append(more, actions);
    }
    var time = document.createElement("time");
    time.textContent = messageTime(item.createdAt);
    if (item.editedAt && !item.deletedByMember) {
      time.appendChild(makeText("small", "cc-member-chat-edited", "수정됨"));
    }
    bubble.appendChild(time);
    row.appendChild(bubble);
    return row;
  }

  function renderDetail(payload, forceBottom) {
    var profile = payload.influencer || {};
    var items = Array.isArray(payload.messages) ? payload.messages : [];
    var signature = items.map(function (item) {
      return [item.id, item.message, item.editedAt, item.deletedByMember].join(":");
    }).join(",");
    setDetailHeader(profile);
    if (signature === detailSignature && !forceBottom) return;
    var nearBottom = detailMessages.scrollHeight - detailMessages.scrollTop - detailMessages.clientHeight < 80;
    detailSignature = signature;
    detailMessages.replaceChildren();
    items.forEach(function (item) {
      detailMessages.appendChild(makeDetailMessage(item, profile));
    });
    if (!items.length) {
      detailMessages.appendChild(makeText("li", "cc-member-chat-detail-empty", "첫 메시지를 보내 대화를 시작해보세요."));
    }
    if (forceBottom || nearBottom) {
      window.requestAnimationFrame(function () {
        detailMessages.scrollTop = detailMessages.scrollHeight;
      });
    }
  }

  async function loadDetail(forceBottom) {
    if (!activeRoom || document.hidden) return;
    if (detailLoading) {
      if (forceBottom) window.setTimeout(function () { loadDetail(true); }, 120);
      return;
    }
    detailLoading = true;
    var roomId = activeRoom.id;
    try {
      var response = await fetch("/api/member/chat?influencer_id=" + encodeURIComponent(roomId), {
        credentials: "same-origin",
        headers: { Accept: "application/json" }
      });
      var payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "대화를 불러오지 못했습니다.");
      if (!activeRoom || activeRoom.id !== roomId) return;
      renderDetail(payload, forceBottom);
      loadRooms();
    } catch (error) {
      if (forceBottom) {
        window.alert(error.message);
        showRoomList();
      }
    } finally {
      detailLoading = false;
    }
  }

  function openDetail(influencerId, seedRoom) {
    var room = roomCache.get(influencerId) || seedRoom || {
      id: influencerId,
      name: influencerId,
      nickname: influencerId,
      image: "/img/no_profile.gif"
    };
    room.id = influencerId;
    root.hidden = false;
    document.body.classList.add("cc-member-chat-available");
    setOpen(true);
    activeRoom = room;
    detailSignature = "";
    setDetailHeader(room);
    detailForm.dataset.influencerId = influencerId;
    listView.hidden = true;
    detailView.hidden = false;
    root.dataset.view = "detail";
    detailMessages.replaceChildren(makeText("li", "cc-member-chat-detail-empty", "대화를 불러오는 중입니다."));
    loadDetail(true);
  }

  function makeRoom(room) {
    var link = document.createElement("a");
    link.className = "cc-member-chat-row";
    link.href = room.href || "/chatlist.php";
    link.dataset.influencerId = room.id;
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
    roomCache = new Map();
    list.replaceChildren();
    rooms.forEach(function (room) {
      roomCache.set(room.id, room);
      list.appendChild(makeRoom(room));
    });
    if (!rooms.length) {
      list.appendChild(makeText("p", "cc-member-chat-empty", "아직 저장된 개인 채팅이 없습니다."));
    }
    root.hidden = rooms.length === 0 && !activeRoom;
    document.body.classList.toggle("cc-member-chat-available", rooms.length > 0 || Boolean(activeRoom));
    if (!rooms.length && !activeRoom) setOpen(false);
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

  detailFile.addEventListener("change", function () {
    var file = detailFile.files && detailFile.files[0];
    if (!file) return clearDetailAttachment();
    if (!window.CandyCastImage) {
      window.alert("이미지 압축기를 불러오지 못했습니다. 페이지를 새로고침해 주세요.");
      return clearDetailAttachment();
    }
    var version = detailAttachmentVersion + 1;
    detailAttachmentVersion = version;
    detailAttachmentBusy = true;
    setDetailBusy();
    detailPreview.hidden = false;
    detailPreview.querySelector("img").removeAttribute("src");
    detailPreview.querySelector("span").textContent = "사진 자동 압축 중...";
    window.CandyCastImage.compress(file).then(function (result) {
      if (version !== detailAttachmentVersion) return;
      detailAttachment = { name: result.name, type: result.type, data: result.data };
      detailPreview.querySelector("img").src = detailAttachment.data;
      detailPreview.querySelector("span").textContent = result.label;
    }).catch(function (error) {
      if (version !== detailAttachmentVersion) return;
      window.alert(error.message);
      clearDetailAttachment();
    }).finally(function () {
      if (version === detailAttachmentVersion) {
        detailAttachmentBusy = false;
        setDetailBusy();
      }
    });
  });

  detailTextarea.addEventListener("input", function () {
    detailTextarea.style.height = "48px";
    detailTextarea.style.height = Math.min(112, detailTextarea.scrollHeight) + "px";
  });

  detailTextarea.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      if (detailForm.requestSubmit) detailForm.requestSubmit();
    }
  });

  detailForm.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!activeRoom || detailSending) return;
    if (detailAttachmentBusy) {
      window.alert("사진 압축이 끝날 때까지 잠시 기다려 주세요.");
      return;
    }
    var message = (detailTextarea.value || "").trim();
    if (!message && !detailAttachment) return;
    detailSending = true;
    setDetailBusy();
    fetch("/api/member/chat/messages", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        influencerId: activeRoom.id,
        message: message,
        attachment: detailAttachment
      })
    }).then(async function (response) {
      var payload = {};
      try { payload = await response.json(); } catch (_error) { payload = {}; }
      if (!response.ok) throw new Error(payload.error || "메시지를 전송하지 못했습니다.");
      detailTextarea.value = "";
      detailTextarea.style.height = "48px";
      clearDetailAttachment();
      detailSignature = "";
      loadDetail(true);
      loadRooms();
    }).catch(function (error) {
      window.alert(error.message);
    }).finally(function () {
      detailSending = false;
      setDetailBusy();
    });
  });

  root.addEventListener("click", function (event) {
    var roomLink = event.target.closest(".cc-member-chat-row[data-influencer-id]");
    if (roomLink) {
      event.preventDefault();
      openDetail(roomLink.dataset.influencerId);
      return;
    }
    var action = event.target.closest("[data-cc-member-chat-action]");
    if (!action) return;
    var name = action.getAttribute("data-cc-member-chat-action");
    if (name === "toggle") setOpen(root.dataset.open !== "true");
    if (name === "close") setOpen(false);
    if (name === "back") {
      showRoomList();
      loadRooms();
    }
    if (name === "attach") detailFile.click();
    if (name === "remove-attachment") clearDetailAttachment();
    if (name === "message-menu") {
      var menu = action.nextElementSibling;
      detailMessages.querySelectorAll(".cc-member-chat-message-actions").forEach(function (item) {
        if (item !== menu) item.hidden = true;
      });
      if (menu) menu.hidden = !menu.hidden;
    }
    if (name === "delete-message") {
      if (!activeRoom || !window.confirm("이 메시지를 삭제할까요?")) return;
      action.disabled = true;
      fetch("/api/member/chat/messages/delete", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ id: action.dataset.messageId, influencerId: activeRoom.id })
      }).then(async function (response) {
        var payload = {};
        try { payload = await response.json(); } catch (_error) { payload = {}; }
        if (!response.ok) throw new Error(payload.error || "메시지를 삭제하지 못했습니다.");
        detailSignature = "";
        loadDetail(true);
        loadRooms();
      }).catch(function (error) {
        window.alert(error.message);
        action.disabled = false;
      });
    }
  });

  document.addEventListener("click", function (event) {
    var externalRoom = event.target.closest("[data-cc-open-member-chat][data-influencer-id]");
    if (externalRoom) {
      event.preventDefault();
      openDetail(externalRoom.dataset.influencerId);
      return;
    }
    if (root.dataset.open === "true" && !root.contains(event.target)) setOpen(false);
    if (
      root.dataset.open === "true" &&
      event.target.closest("#cc-support-root [data-cc-support-action]")
    ) {
      setOpen(false);
    }
  }, true);

  document.addEventListener("keydown", function (event) {
    var externalRoom = event.target.closest("[data-cc-open-member-chat][data-influencer-id]");
    if (
      externalRoom && (event.key === "Enter" || event.key === " ") &&
      !event.isComposing && event.keyCode !== 229
    ) {
      event.preventDefault();
      openDetail(externalRoom.dataset.influencerId);
      return;
    }
    if (event.key === "Escape" && root.dataset.open === "true") {
      setOpen(false);
      launcher.focus();
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) {
      loadRooms();
      loadDetail(false);
    }
  });

  var messages = document.querySelector(".cc-chat-messages");
  if (messages) messages.scrollTop = messages.scrollHeight;

  initializeDetailComposer();

  loadRooms();
  pollTimer = window.setInterval(function () {
    loadRooms();
    loadDetail(false);
  }, 10000);
  window.addEventListener("pagehide", function () {
    window.clearInterval(pollTimer);
  }, { once: true });
}());
