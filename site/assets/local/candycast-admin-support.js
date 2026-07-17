(function () {
  "use strict";
  var app = document.querySelector(".cc-admin-support");
  if (!app || app.dataset.bound === "1") return;
  app.dataset.bound = "1";

  var state = { rooms: [], selectedId: 0, detail: null, listBusy: false, roomBusy: false, attachment: null, signature: "", mobileListMode: false, editingId: 0 };
  var queueNames = { important: "중요상담함", uda: "우다상담함", normal: "일반상담함", bura: "부라상담함" };
  var queueShort = { important: "중요", uda: "우다", normal: "일반", bura: "부라" };
  var statusNode = document.getElementById("cc-support-admin-connection");
  var roomTitle = document.getElementById("cc-support-room-title");
  var roomMeta = document.getElementById("cc-support-room-meta");
  var chatLog = document.getElementById("cc-support-staff-messages");
  var previewLog = document.getElementById("cc-support-preview-messages");
  var form = document.getElementById("cc-support-staff-send");
  var textarea = form.querySelector("textarea");
  var fileInput = document.getElementById("cc-support-staff-file");
  var attachmentPreview = document.getElementById("cc-support-admin-attachment");
  var clearButton = document.getElementById("cc-support-clear-room");
  var closeButton = document.getElementById("cc-support-close-room");
  var refreshButton = document.getElementById("cc-support-admin-refresh");
  var pollTimer = 0;
  var attachmentBusy = false;
  var attachmentVersion = 0;
  var POLL_INTERVAL_MS = 5000;
  var POLL_JITTER_MS = 500;

  function request(url, options) {
    var settings = options || {};
    settings.headers = Object.assign({ "Accept": "application/json" }, settings.headers || {});
    return fetch(url, settings).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) throw new Error(body.error || "요청을 처리하지 못했습니다.");
        return body;
      });
    });
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char];
    });
  }

  function setConnection(ok, text) {
    statusNode.classList.toggle("error", !ok);
    statusNode.textContent = text || (ok ? "실시간 연결됨" : "연결 확인 중");
  }

  function roomName(room) {
    return room.nickname || room.memberId || "회원";
  }

  function roomEntry(room) {
    var unread = Number(room.staffUnread) || 0;
    var active = Number(room.id) === Number(state.selectedId) ? " active" : "";
    var unreadClass = unread ? " has-unread" : "";
    var actions = Object.keys(queueNames).filter(function (queue) { return queue !== room.queue; }).slice(0, 3).map(function (queue) {
      return "<button type=\"button\" data-room-action=\"queue\" data-room-id=\"" + room.id + "\" data-room-queue=\"" + queue + "\">" + queueShort[queue] + "</button>";
    }).join("");
    actions += "<button type=\"button\" data-room-action=\"close\" data-room-id=\"" + room.id + "\">나가기</button>";
    return "<article class=\"cc-support-room" + active + unreadClass + "\" data-room=\"" + room.id + "\">" +
      "<button type=\"button\" class=\"cc-support-room-main\" data-room-action=\"open\" data-room-id=\"" + room.id + "\">" +
      "<strong>" + escapeHtml(roomName(room)) + "</strong>" +
      (unread ? "<i class=\"cc-support-room-unread\">" + (unread > 99 ? "99+" : unread) + "</i>" : "") +
      "<small>" + escapeHtml(room.memberId) + " · " + escapeHtml(room.updatedAt || "") + "</small>" +
      "<span>" + escapeHtml(room.lastMessage || "첫 상담") + "</span></button>" +
      "<div class=\"cc-support-room-actions\">" + actions + "</div></article>";
  }

  function renderRooms() {
    Object.keys(queueNames).forEach(function (queue) {
      var rooms = state.rooms.filter(function (room) { return room.status === "open" && room.queue === queue; });
      var list = document.querySelector("[data-queue-list=\"" + queue + "\"]");
      var count = document.querySelector("[data-queue-count=\"" + queue + "\"]");
      count.textContent = String(rooms.length);
      list.innerHTML = rooms.length ? rooms.map(roomEntry).join("") : "<p class=\"cc-support-room-empty\">상담방이 없습니다.</p>";
    });
  }

  function updateGlobalBadge() {
    var badge = document.getElementById("cc-admin-support-unread");
    if (!badge) return;
    var count = state.rooms.reduce(function (total, room) { return total + (Number(room.staffUnread) || 0); }, 0);
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.hidden = count === 0;
  }

  function refreshRooms(selectWhenEmpty) {
    if (state.listBusy) return Promise.resolve();
    state.listBusy = true;
    return request("/api/admin/support/rooms")
      .then(function (body) {
        state.rooms = Array.isArray(body.rooms) ? body.rooms : [];
        updateGlobalBadge();
        if (state.selectedId && !state.rooms.some(function (room) { return Number(room.id) === Number(state.selectedId) && room.status === "open"; })) {
          resetSelection();
        }
        renderRooms();
        if (selectWhenEmpty && !state.selectedId) {
          var first = state.rooms.find(function (room) { return room.status === "open" && Number(room.staffUnread) > 0; }) || state.rooms.find(function (room) { return room.status === "open"; });
          if (first) return openRoom(first.id);
        }
        setConnection(true, "실시간 연결됨");
      })
      .catch(function (error) { setConnection(false, error.message); })
      .finally(function () { state.listBusy = false; });
  }

  function timeLabel(value) {
    var match = String(value || "").match(/(\d{2}):(\d{2})/);
    return match ? match[1] + ":" + match[2] : String(value || "");
  }

  function attachmentMarkup(item) {
    if (!item.attachment || !item.attachment.data) return "";
    return "<img src=\"" + escapeHtml(item.attachment.data) + "\" alt=\"" + escapeHtml(item.attachment.name || "첨부 이미지") + "\">";
  }

  function staffMessage(item) {
    var side = item.senderType === "staff" ? "staff" : "member";
    var name = side === "staff" ? "상담사" : (state.detail && roomName(state.detail.room));
    var deleted = Boolean(item.deletedByMember);
    var message = deleted ? "삭제된 메시지입니다." : (item.message || "");
    var editButton = deleted ? "" : "<button type=\"button\" data-message-action=\"edit\" data-message-id=\"" + item.id + "\">수정</button>";
    var edited = item.editedAt && !deleted ? "<small class=\"cc-support-admin-edited\">수정됨</small>" : "";
    return "<div class=\"cc-support-admin-message " + side + (deleted ? " is-deleted" : "") + "\" data-message-id=\"" + item.id + "\"><div>" +
      "<b>" + escapeHtml(name || "회원") + "</b><p>" + escapeHtml(message) + (deleted ? "" : attachmentMarkup(item)) + "</p>" +
      "<span class=\"cc-support-admin-message-tools\">" + editButton + "<button type=\"button\" data-message-action=\"delete\" data-message-id=\"" + item.id + "\">삭제</button></span>" +
      "<div class=\"cc-support-admin-inline-editor\" hidden><textarea maxlength=\"" + 1000 + "\" rows=\"2\">" + escapeHtml(item.message || "") + "</textarea><span><button type=\"button\" data-message-action=\"save\" data-message-id=\"" + item.id + "\">저장</button><button type=\"button\" data-message-action=\"cancel\">취소</button></span></div>" +
      "<time>" + escapeHtml(timeLabel(item.createdAt)) + edited + "</time></div></div>";
  }

  function previewMessage(item) {
    var side = item.senderType === "staff" ? "staff" : "member";
    var message = item.deletedByMember ? "삭제된 메시지입니다." : (item.message || "");
    return "<div class=\"cc-support-preview-message " + side + "\">" +
      (side === "staff" ? "<img class=\"cc-support-preview-avatar\" src=\"/assets/local/candycast_operator.png\" alt=\"캔디캐스트 상담원\">" : "") +
      "<div>" + (side === "staff" ? "<b>고객센터</b>" : "") + "<p>" + escapeHtml(message) + (item.deletedByMember ? "" : attachmentMarkup(item)) + "</p></div></div>";
  }

  function renderMessages(items, force) {
    var list = Array.isArray(items) ? items : [];
    var signature = list.map(function (item) {
      return [item.id, item.message, item.editedAt, item.deletedByMember].join(":");
    }).join(",");
    if (!force && signature === state.signature) return;
    var nearBottom = chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 90;
    state.signature = signature;
    chatLog.innerHTML = list.length ? list.map(staffMessage).join("") : "<div class=\"cc-support-staff-empty\">아직 대화가 없습니다.</div>";
    previewLog.innerHTML = list.length ? list.map(previewMessage).join("") : "<div class=\"cc-support-staff-empty\">회원 화면 미리보기</div>";
    if (nearBottom || !chatLog.dataset.loaded) chatLog.scrollTop = chatLog.scrollHeight;
    previewLog.scrollTop = previewLog.scrollHeight;
    chatLog.dataset.loaded = "1";
  }

  function resetSelection() {
    state.selectedId = 0;
    state.detail = null;
    state.signature = "";
    clearAttachment();
    roomTitle.textContent = "상담방을 선택하세요.";
    roomMeta.textContent = "왼쪽 상담함에서 회원을 선택하세요.";
    chatLog.innerHTML = "<div class=\"cc-support-staff-empty\">상담방을 선택하면 대화가 표시됩니다.</div>";
    previewLog.innerHTML = "<div class=\"cc-support-staff-empty\">회원 화면 미리보기</div>";
    clearButton.disabled = true;
    closeButton.disabled = true;
    form.querySelector("button[data-admin-action=send]").disabled = true;
    form.querySelector("button[data-admin-action=attach]").disabled = true;
    app.classList.remove("chat-selected");
    renderRooms();
  }

  function openRoom(id, quiet, refreshList) {
    if (!id || state.roomBusy) return Promise.resolve();
    state.roomBusy = true;
    state.selectedId = Number(id);
    if (!quiet) {
      state.editingId = 0;
      state.signature = "";
      state.mobileListMode = false;
    }
    renderRooms();
    return request("/api/admin/support/rooms/" + encodeURIComponent(id))
      .then(function (body) {
        state.detail = body;
        var room = body.room;
        roomTitle.textContent = roomName(room);
        roomMeta.textContent = room.memberId + " · " + (room.name || "이름 미등록") + " · 캔디 " + Number(room.balance || 0).toLocaleString("ko-KR");
        clearButton.disabled = false;
        closeButton.disabled = false;
        setAttachmentBusy(attachmentBusy);
        renderMessages(body.messages, !quiet);
        app.classList.toggle("chat-selected", !state.mobileListMode || window.innerWidth > 900);
        if (refreshList === false) return null;
        return refreshRooms(false);
      })
      .catch(function (error) { setConnection(false, error.message); })
      .finally(function () { state.roomBusy = false; });
  }

  function postRoom(id, action, body) {
    return request("/api/admin/support/rooms/" + encodeURIComponent(id) + "/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
  }

  function clearAttachment() {
    attachmentVersion += 1;
    state.attachment = null;
    setAttachmentBusy(false);
    fileInput.value = "";
    attachmentPreview.hidden = true;
    attachmentPreview.querySelector("img").removeAttribute("src");
    attachmentPreview.querySelector("span").textContent = "";
  }

  function setAttachmentBusy(value) {
    attachmentBusy = Boolean(value);
    form.querySelector("button[data-admin-action=send]").disabled = attachmentBusy || !state.selectedId;
    form.querySelector("button[data-admin-action=attach]").disabled = attachmentBusy || !state.selectedId;
  }

  function schedulePolling() {
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = 0;
    if (document.hidden) return;
    pollTimer = window.setTimeout(function () {
      pollTimer = 0;
      var selectedId = state.selectedId;
      var requests = [refreshRooms(false)];
      if (selectedId && !state.editingId) requests.push(openRoom(selectedId, true, false));
      Promise.all(requests).finally(schedulePolling);
    }, POLL_INTERVAL_MS + Math.floor(Math.random() * POLL_JITTER_MS));
  }

  document.querySelector(".cc-support-queues").addEventListener("click", function (event) {
    var button = event.target.closest("[data-room-action]");
    if (!button) return;
    var id = Number(button.dataset.roomId);
    var action = button.dataset.roomAction;
    if (action === "open") return openRoom(id);
    if (action === "queue") {
      postRoom(id, "queue", { queue: button.dataset.roomQueue }).then(function () { return refreshRooms(false); }).catch(function (error) { window.alert(error.message); });
    } else if (action === "close") {
      if (!window.confirm("이 상담방을 종료할까요? 대화 기록은 보관됩니다.")) return;
      postRoom(id, "close").then(function () { if (state.selectedId === id) resetSelection(); return refreshRooms(true); }).catch(function (error) { window.alert(error.message); });
    }
  });

  chatLog.addEventListener("click", function (event) {
    var button = event.target.closest("[data-message-action]");
    if (!button || !state.selectedId) return;
    var action = button.dataset.messageAction;
    var message = button.closest(".cc-support-admin-message");
    var editor = message && message.querySelector(".cc-support-admin-inline-editor");
    var messageId = Number(button.dataset.messageId || 0);
    if (action === "edit") {
      state.editingId = messageId;
      editor.hidden = false;
      editor.querySelector("textarea").focus();
      return;
    }
    if (action === "cancel") {
      state.editingId = 0;
      editor.hidden = true;
      return;
    }
    if (action === "save") {
      var nextMessage = editor.querySelector("textarea").value.trim();
      if (!nextMessage) {
        window.alert("메시지를 입력해 주세요.");
        return;
      }
      button.disabled = true;
      postRoom(state.selectedId, "edit-message", { id: messageId, message: nextMessage })
        .then(function () {
          state.editingId = 0;
          state.signature = "";
          return openRoom(state.selectedId, true);
        })
        .catch(function (error) {
          button.disabled = false;
          window.alert(error.message);
        });
      return;
    }
    if (action === "delete") {
      if (!window.confirm("이 메시지를 삭제할까요?")) return;
      button.disabled = true;
      postRoom(state.selectedId, "delete-message", { id: messageId })
        .then(function () {
          state.editingId = 0;
          state.signature = "";
          return openRoom(state.selectedId, true);
        })
        .catch(function (error) {
          button.disabled = false;
          window.alert(error.message);
        });
    }
  });

  document.querySelector("[data-admin-action=mobile-back]").addEventListener("click", function () {
    state.mobileListMode = true;
    app.classList.remove("chat-selected");
  });
  refreshButton.addEventListener("click", function () {
    refreshRooms(false).then(function () {
      if (state.selectedId) return openRoom(state.selectedId, true, false);
      return null;
    });
  });
  closeButton.addEventListener("click", function () {
    if (!state.selectedId || !window.confirm("이 상담방을 종료할까요? 대화 기록은 보관됩니다.")) return;
    postRoom(state.selectedId, "close").then(function () { resetSelection(); return refreshRooms(true); }).catch(function (error) { window.alert(error.message); });
  });
  clearButton.addEventListener("click", function () {
    if (!state.selectedId || !window.confirm("이 상담방의 채팅 내용을 모두 비울까요?")) return;
    postRoom(state.selectedId, "clear").then(function () { state.signature = ""; return openRoom(state.selectedId); }).catch(function (error) { window.alert(error.message); });
  });

  form.querySelector("[data-admin-action=attach]").addEventListener("click", function () { fileInput.click(); });
  form.querySelector("[data-admin-action=remove-attachment]").addEventListener("click", clearAttachment);
  form.querySelector("[data-admin-action=send]").addEventListener("click", function () {
    if (form.requestSubmit) form.requestSubmit();
  });
  fileInput.addEventListener("change", function () {
    var file = fileInput.files && fileInput.files[0];
    if (!file) return clearAttachment();
    if (!window.CandyCastImage) {
      window.alert("이미지 압축기를 불러오지 못했습니다. 페이지를 새로고침해 주세요.");
      return clearAttachment();
    }
    var version = attachmentVersion + 1;
    attachmentVersion = version;
    setAttachmentBusy(true);
    attachmentPreview.hidden = false;
    attachmentPreview.querySelector("img").removeAttribute("src");
    attachmentPreview.querySelector("span").textContent = "사진 자동 압축 중...";
    window.CandyCastImage.compress(file).then(function (result) {
      if (version !== attachmentVersion) return;
      state.attachment = { name: result.name, type: result.type, data: result.data };
      attachmentPreview.querySelector("img").src = state.attachment.data;
      attachmentPreview.querySelector("span").textContent = result.label;
      attachmentPreview.hidden = false;
    }).catch(function (error) {
      if (version !== attachmentVersion) return;
      window.alert(error.message);
      clearAttachment();
    }).finally(function () {
      if (version === attachmentVersion) setAttachmentBusy(false);
    });
  });

  textarea.addEventListener("input", function () {
    textarea.style.height = "40px";
    textarea.style.height = Math.min(120, textarea.scrollHeight) + "px";
  });
  textarea.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); if (form.requestSubmit) form.requestSubmit(); }
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!state.selectedId) return;
    if (attachmentBusy) {
      window.alert("사진 압축이 끝날 때까지 잠시 기다려 주세요.");
      return;
    }
    var message = (textarea.value || "").trim();
    if (!message && !state.attachment) return;
    var sendButton = form.querySelector("button[data-admin-action=send]");
    sendButton.disabled = true;
    postRoom(state.selectedId, "messages", { message: message, attachment: state.attachment })
      .then(function () {
        textarea.value = "";
        textarea.style.height = "40px";
        clearAttachment();
        state.signature = "";
        return openRoom(state.selectedId);
      })
      .catch(function (error) { window.alert(error.message); })
      .finally(function () { setAttachmentBusy(attachmentBusy); textarea.focus(); });
  });

  refreshRooms(true);
  schedulePolling();
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (pollTimer) window.clearTimeout(pollTimer);
      pollTimer = 0;
      return;
    }
    var selectedId = state.selectedId;
    var requests = [refreshRooms(false)];
    if (selectedId && !state.editingId) requests.push(openRoom(selectedId, true, false));
    Promise.all(requests).finally(schedulePolling);
  });
})();
