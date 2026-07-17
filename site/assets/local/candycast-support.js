(function () {
  "use strict";

  var root = document.getElementById("cc-support-root");
  if (!root || root.dataset.bound === "1") return;
  root.dataset.bound = "1";

  var loggedIn = root.dataset.loggedIn === "1";
  var panel = root.querySelector(".cc-support-panel");
  var home = root.querySelector(".cc-support-home");
  var chat = root.querySelector(".cc-support-chat");
  var login = root.querySelector(".cc-support-login");
  var messages = root.querySelector(".cc-support-messages");
  var form = root.querySelector(".cc-support-composer");
  var textarea = form ? form.querySelector("textarea") : null;
  var fileInput = root.querySelector("#cc-support-file");
  var preview = root.querySelector(".cc-support-attachment-preview");
  var unreadBadge = root.querySelector(".cc-support-unread");
  var pendingAttachment = null;
  var attachmentBusy = false;
  var attachmentVersion = 0;
  var messageSignature = "";
  var activeMode = "home";
  var pollTimer = 0;
  var unreadTimer = 0;
  var loadingMessages = false;
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

  function setUnread(value) {
    var count = Math.max(0, Number(value) || 0);
    if (!unreadBadge) return;
    unreadBadge.textContent = count > 99 ? "99+" : String(count);
    unreadBadge.hidden = count === 0;
  }

  function setMode(mode) {
    var isChat = mode === "chat";
    activeMode = isChat ? "chat" : "home";
    home.hidden = isChat;
    chat.hidden = !isChat;
    root.querySelectorAll(".cc-support-home-nav [data-cc-support-action]").forEach(function (button) {
      if (button.dataset.ccSupportAction === activeMode) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (!isChat) stopMessagePolling();
    if (isChat) {
      login.hidden = loggedIn;
      messages.hidden = !loggedIn;
      form.hidden = !loggedIn;
      if (loggedIn) {
        loadMessages(true).finally(startMessagePolling);
        window.setTimeout(function () { if (textarea) textarea.focus(); }, 220);
      }
    }
  }

  function setOpen(open) {
    root.dataset.open = open ? "true" : "false";
    document.body.classList.toggle("cc-support-open", open);
    panel.setAttribute("aria-hidden", open ? "false" : "true");
    var launcher = root.querySelector(".cc-support-fab");
    launcher.setAttribute("aria-expanded", open ? "true" : "false");
    launcher.setAttribute("aria-label", open ? "고객센터 닫기" : "고객센터 열기");
    if (!open) {
      stopMessagePolling();
    }
  }

  function timeLabel(value) {
    if (!value) return "";
    var match = String(value).match(/(\d{2}):(\d{2})/);
    return match ? match[1] + ":" + match[2] : String(value);
  }

  function makeMessage(item) {
    var mine = item.senderType === "member";
    var row = document.createElement("div");
    row.className = "cc-support-message " + (mine ? "member" : "staff");
    row.dataset.messageId = item.id;
    if (!mine) {
      var avatar = document.createElement("img");
      avatar.className = "cc-support-message-avatar";
      avatar.src = "/assets/local/candycast_operator.png";
      avatar.alt = "캔디캐스트 상담원";
      row.appendChild(avatar);
    }
    var body = document.createElement("div");
    body.className = "cc-support-message-body";
    if (!mine) {
      var name = document.createElement("p");
      name.className = "cc-support-message-name";
      name.textContent = "고객센터";
      body.appendChild(name);
    }
    var bubble = document.createElement("div");
    bubble.className = "cc-support-message-bubble";
    if (item.deletedByMember) {
      bubble.classList.add("is-deleted");
      bubble.textContent = "삭제된 메시지입니다.";
    } else if (item.message) {
      bubble.appendChild(document.createTextNode(item.message));
    }
    if (!item.deletedByMember && item.attachment && item.attachment.data) {
      var image = document.createElement("img");
      image.className = "cc-support-message-image";
      image.src = item.attachment.data;
      image.alt = item.attachment.name || "첨부 이미지";
      bubble.appendChild(image);
    }
    body.appendChild(bubble);
    var time = document.createElement("time");
    time.textContent = timeLabel(item.createdAt);
    if (item.editedAt && !item.deletedByMember) {
      var edited = document.createElement("small");
      edited.className = "cc-support-message-edited";
      edited.textContent = "수정됨";
      time.appendChild(edited);
    }
    body.appendChild(time);
    if (mine && !item.deletedByMember) {
      var more = document.createElement("button");
      more.type = "button";
      more.className = "cc-support-message-more";
      more.dataset.ccSupportAction = "message-menu";
      more.setAttribute("aria-label", "메시지 옵션");
      more.textContent = "⋮";
      var actions = document.createElement("span");
      actions.className = "cc-support-message-actions";
      actions.hidden = true;
      var remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.ccSupportAction = "delete-message";
      remove.dataset.messageId = item.id;
      remove.textContent = "메시지 삭제";
      actions.appendChild(remove);
      body.append(more, actions);
    }
    row.appendChild(body);
    return row;
  }

  function renderMessages(items) {
    var list = Array.isArray(items) ? items : [];
    var nextSignature = list.map(function (item) {
      return [item.id, item.message, item.editedAt, item.deletedByMember].join(":");
    }).join(",");
    if (nextSignature === messageSignature) return;
    var nearBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
    messageSignature = nextSignature;
    messages.textContent = "";
    if (!list.length) {
      var empty = document.createElement("p");
      empty.className = "cc-support-empty";
      empty.textContent = "상담 내용을 입력해 주세요.";
      messages.appendChild(empty);
      return;
    }
    list.forEach(function (item) { messages.appendChild(makeMessage(item)); });
    if (nearBottom || !messages.dataset.loaded) messages.scrollTop = messages.scrollHeight;
    messages.dataset.loaded = "1";
  }

  function loadMessages(markRead) {
    if (!loggedIn || loadingMessages) return Promise.resolve();
    loadingMessages = true;
    return request("/api/support/room?mark_read=" + (markRead ? "1" : "0"))
      .then(function (body) {
        renderMessages(body.messages);
        setUnread(body.room ? body.room.memberUnread : 0);
      })
      .catch(function (error) {
        if (messages && !messageSignature) {
          messages.innerHTML = "<p class=\"cc-support-empty\"></p>";
          messages.firstChild.textContent = error.message;
        }
      })
      .finally(function () { loadingMessages = false; });
  }

  function startMessagePolling() {
    stopMessagePolling();
    if (!loggedIn || document.hidden || root.dataset.open !== "true" || activeMode !== "chat") return;
    pollTimer = window.setTimeout(function () {
      pollTimer = 0;
      loadMessages(true).finally(startMessagePolling);
    }, POLL_INTERVAL_MS + Math.floor(Math.random() * POLL_JITTER_MS));
  }

  function stopMessagePolling() {
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = 0;
  }

  function pollUnread() {
    if (!loggedIn || document.hidden || (root.dataset.open === "true" && activeMode === "chat")) return Promise.resolve();
    return request("/api/support/unread")
      .then(function (body) { setUnread(body.unread); })
      .catch(function () {});
  }

  function startUnreadPolling() {
    if (unreadTimer) window.clearTimeout(unreadTimer);
    unreadTimer = 0;
    if (!loggedIn || document.hidden) return;
    unreadTimer = window.setTimeout(function () {
      unreadTimer = 0;
      pollUnread().finally(startUnreadPolling);
    }, POLL_INTERVAL_MS + Math.floor(Math.random() * POLL_JITTER_MS));
  }

  function setAttachmentBusy(value) {
    attachmentBusy = Boolean(value);
    var attachButton = form && form.querySelector(".cc-support-attach");
    var sendButton = form && form.querySelector(".cc-support-send");
    if (attachButton) attachButton.disabled = attachmentBusy;
    if (sendButton) sendButton.disabled = attachmentBusy;
  }

  function clearAttachment() {
    attachmentVersion += 1;
    pendingAttachment = null;
    setAttachmentBusy(false);
    if (fileInput) fileInput.value = "";
    if (preview) {
      preview.hidden = true;
      preview.querySelector("img").removeAttribute("src");
      preview.querySelector("span").textContent = "";
    }
  }

  root.addEventListener("click", function (event) {
    var actionElement = event.target.closest("[data-cc-support-action]");
    if (!actionElement) return;
    var action = actionElement.dataset.ccSupportAction;
    if (action === "message-menu") {
      var actionMenu = actionElement.nextElementSibling;
      root.querySelectorAll(".cc-support-message-actions").forEach(function (menu) {
        if (menu !== actionMenu) menu.hidden = true;
      });
      if (actionMenu) actionMenu.hidden = !actionMenu.hidden;
    } else if (action === "delete-message") {
      if (!window.confirm("이 메시지를 삭제할까요?")) return;
      actionElement.disabled = true;
      request("/api/support/messages/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: actionElement.dataset.messageId })
      }).then(function () {
        messageSignature = "";
        return loadMessages(true);
      }).catch(function (error) {
        window.alert(error.message);
      }).finally(function () {
        actionElement.disabled = false;
      });
    } else if (action === "open") {
      setOpen(true);
      setMode("home");
    } else if (action === "toggle") {
      var nextOpen = root.dataset.open !== "true";
      setOpen(nextOpen);
      if (nextOpen) setMode("home");
    } else if (action === "home" || action === "back") {
      setMode("home");
    } else if (action === "chat") {
      setMode("chat");
    } else if (action === "close") {
      setOpen(false);
    } else if (action === "attach" && fileInput) {
      fileInput.click();
    } else if (action === "remove-attachment") {
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
      setAttachmentBusy(true);
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
        if (version === attachmentVersion) setAttachmentBusy(false);
      });
    });
  }

  if (textarea) {
    textarea.addEventListener("input", function () {
      textarea.style.height = "40px";
      textarea.style.height = Math.min(92, textarea.scrollHeight) + "px";
    });
    textarea.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (form.requestSubmit) form.requestSubmit();
      }
    });
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (attachmentBusy) {
        window.alert("사진 압축이 끝날 때까지 잠시 기다려 주세요.");
        return;
      }
      var message = (textarea.value || "").trim();
      if (!message && !pendingAttachment) return;
      var submit = form.querySelector(".cc-support-send");
      submit.disabled = true;
      request("/api/support/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: message, attachment: pendingAttachment })
      }).then(function () {
        textarea.value = "";
        textarea.style.height = "40px";
        clearAttachment();
        messageSignature = "";
        return loadMessages(true);
      }).catch(function (error) {
        window.alert(error.message);
      }).finally(function () { submit.disabled = false; });
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && root.dataset.open === "true") setOpen(false);
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      stopMessagePolling();
      if (unreadTimer) window.clearTimeout(unreadTimer);
      unreadTimer = 0;
      return;
    }
    pollUnread().finally(startUnreadPolling);
    if (root.dataset.open === "true" && activeMode === "chat") {
      loadMessages(true).finally(startMessagePolling);
    }
  });

  if (loggedIn) {
    pollUnread().finally(startUnreadPolling);
  }
  setMode("home");
  setOpen(false);
})();
