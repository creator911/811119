(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const state = {
    members: [],
    influencers: [],
    rooms: [],
    memberId: "",
    influencerId: "",
    pollTimer: 0,
  };
  const POLL_INTERVAL_MS = 5000;
  const POLL_JITTER_MS = 500;

  async function request(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      payload = {};
    }
    if (!response.ok) throw new Error(payload.error || "요청을 처리하지 못했습니다.");
    return payload;
  }

  function showToast(message, error = false) {
    const toast = $("#cc-admin-toast");
    if (!toast) return;
    toast.textContent = message;
    toast.classList.toggle("is-error", error);
    toast.hidden = false;
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => { toast.hidden = true; }, 3200);
  }

  function debounce(callback, delay = 260) {
    let timer = 0;
    return (...args) => {
      clearTimeout(timer);
      timer = window.setTimeout(() => callback(...args), delay);
    };
  }

  function matchesTerm(values, term) {
    if (!term) return true;
    return values.join(" ").toLocaleLowerCase("ko-KR").includes(term.toLocaleLowerCase("ko-KR"));
  }

  function populateMemberSelect(search = "") {
    const select = $("#cc-chat-member-select");
    if (!select) return;
    const current = state.memberId || select.value;
    select.replaceChildren(new Option("회원을 선택하세요", ""));
    for (const member of state.members) {
      if (!matchesTerm([member.id, member.nickname, member.name, member.phone], search)) continue;
      select.append(new Option(`${member.nickname || member.name || member.id} (${member.id})`, member.id));
    }
    if (Array.from(select.options).some((option) => option.value === current)) select.value = current;
  }

  function populateInfluencerSelect(search = "") {
    const select = $("#cc-chat-bj-select");
    if (!select) return;
    const current = state.influencerId || select.value;
    select.replaceChildren(new Option("BJ를 선택하세요", ""));
    for (const profile of state.influencers) {
      if (!matchesTerm([profile.id, profile.name, profile.nickname], search)) continue;
      select.append(new Option(`${profile.name || profile.nickname || profile.id} (${profile.id})`, profile.id));
    }
    if (Array.from(select.options).some((option) => option.value === current)) select.value = current;
  }

  async function loadParties() {
    const [membersPayload, influencersPayload] = await Promise.all([
      request("/api/admin/members"),
      request("/api/admin/influencers"),
    ]);
    state.members = membersPayload.members || [];
    state.influencers = influencersPayload.influencers || [];
    populateMemberSelect($("#cc-chat-member-search")?.value || "");
    populateInfluencerSelect($("#cc-chat-bj-search")?.value || "");
  }

  function roomKey(memberId, influencerId) {
    return `${memberId}\u0000${influencerId}`;
  }

  function renderRooms() {
    const list = $("#cc-chat-room-list");
    if (!list) return;
    list.replaceChildren();
    if (!state.rooms.length) {
      const empty = document.createElement("p");
      empty.className = "cc-admin-empty";
      empty.textContent = "아직 개인 채팅이 없습니다. 오른쪽에서 회원과 BJ를 선택해 시작할 수 있습니다.";
      list.append(empty);
      return;
    }
    const activeKey = roomKey(state.memberId, state.influencerId);
    for (const room of state.rooms) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `cc-chat-room-item${roomKey(room.memberId, room.influencer.id) === activeKey ? " is-active" : ""}`;
      button.dataset.memberId = room.memberId;
      button.dataset.influencerId = room.influencer.id;

      const image = document.createElement("img");
      image.src = room.influencer.image || "/img/no_profile.gif";
      image.alt = "";
      image.loading = "lazy";
      const text = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = `${room.memberName} ↔ ${room.influencer.name || room.influencer.id}`;
      const preview = document.createElement("span");
      preview.textContent = room.lastMessage;
      text.append(title, preview);
      const side = document.createElement("span");
      const time = document.createElement("small");
      time.textContent = room.lastAt || "";
      side.append(time);
      if (room.unread) {
        const badge = document.createElement("span");
        badge.className = "cc-chat-room-badge";
        badge.textContent = room.unread > 99 ? "99+" : String(room.unread);
        side.append(badge);
      }
      button.append(image, text, side);
      list.append(button);
    }
  }

  async function loadRooms(search = "") {
    const payload = await request(`/api/admin/member-chat/rooms?q=${encodeURIComponent(search)}`);
    state.rooms = payload.rooms || [];
    renderRooms();
  }

  function renderConversation(payload, preservePosition = false) {
    const messages = $("#cc-chat-admin-messages");
    const meta = $("#cc-chat-active-meta");
    const composer = $("#cc-chat-admin-composer");
    if (!messages || !meta || !composer) return;
    const oldBottomDistance = messages.scrollHeight - messages.scrollTop - messages.clientHeight;
    messages.replaceChildren();

    if (!payload.messages?.length) {
      const empty = document.createElement("p");
      empty.className = "cc-admin-empty";
      empty.textContent = "첫 메시지를 보내 대화를 시작하세요.";
      messages.append(empty);
    } else {
      for (const item of payload.messages) {
        const article = document.createElement("article");
        article.className = `cc-chat-message${item.sender === "influencer" ? " is-influencer" : " is-member"}`;
        const bubble = document.createElement("div");
        const sender = document.createElement("strong");
        sender.textContent = item.sender === "influencer"
          ? payload.influencer.name || payload.influencer.id
          : payload.member.nickname || payload.member.id;
        const text = document.createElement("p");
        text.textContent = item.message;
        const time = document.createElement("time");
        time.textContent = item.createdAt;
        bubble.append(sender, text, time);
        article.append(bubble);
        messages.append(article);
      }
    }
    meta.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = `${payload.member.nickname || payload.member.id} 회원 · ${payload.influencer.name || payload.influencer.id} BJ`;
    const detail = document.createElement("span");
    detail.textContent = `${payload.member.id} / ${payload.member.phone || "전화번호 없음"}`;
    meta.append(title, detail);
    composer.elements.message.disabled = false;
    composer.querySelector("button").disabled = false;
    if (!preservePosition || oldBottomDistance < 80) messages.scrollTop = messages.scrollHeight;
  }

  async function openConversation(memberId, influencerId, preservePosition = false) {
    if (!memberId || !influencerId) {
      showToast("회원과 보내는 BJ를 모두 선택해주세요.", true);
      return;
    }
    state.memberId = memberId;
    state.influencerId = influencerId;
    $("#cc-chat-member-select").value = memberId;
    $("#cc-chat-bj-select").value = influencerId;
    const payload = await request(
      `/api/admin/member-chat/messages?member_id=${encodeURIComponent(memberId)}&influencer_id=${encodeURIComponent(influencerId)}`,
    );
    renderConversation(payload, preservePosition);
    renderRooms();
  }

  async function sendMessage(form, button) {
    if (!state.memberId || !state.influencerId) {
      showToast("회원과 보내는 BJ를 선택해주세요.", true);
      return;
    }
    const message = form.elements.message.value.trim();
    if (!message) return;
    button.disabled = true;
    try {
      await request("/api/admin/member-chat/messages", {
        method: "POST",
        body: JSON.stringify({
          memberId: state.memberId,
          influencerId: state.influencerId,
          message,
        }),
      });
      form.reset();
      await Promise.all([
        openConversation(state.memberId, state.influencerId),
        loadRooms($("#cc-chat-room-search")?.value || ""),
      ]);
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  function schedulePoll() {
    clearTimeout(state.pollTimer);
    state.pollTimer = window.setTimeout(async () => {
      try {
        await loadRooms($("#cc-chat-room-search")?.value || "");
        if (state.memberId && state.influencerId) {
          await openConversation(state.memberId, state.influencerId, true);
        }
      } catch (_error) {
        // The next cycle retries transient failures without interrupting typing.
      } finally {
        schedulePoll();
      }
    }, POLL_INTERVAL_MS + Math.floor(Math.random() * POLL_JITTER_MS));
  }

  $("#cc-chat-member-search")?.addEventListener("input", (event) => populateMemberSelect(event.target.value));
  $("#cc-chat-bj-search")?.addEventListener("input", (event) => populateInfluencerSelect(event.target.value));
  $("#cc-chat-room-search")?.addEventListener("input", debounce((event) => {
    loadRooms(event.target.value).catch((error) => showToast(error.message, true));
  }));
  $("#cc-chat-open")?.addEventListener("click", () => {
    openConversation($("#cc-chat-member-select").value, $("#cc-chat-bj-select").value)
      .catch((error) => showToast(error.message, true));
  });
  $("#cc-chat-room-list")?.addEventListener("click", (event) => {
    const room = event.target.closest(".cc-chat-room-item");
    if (!room) return;
    openConversation(room.dataset.memberId, room.dataset.influencerId)
      .catch((error) => showToast(error.message, true));
  });
  $("#cc-chat-admin-composer")?.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage(event.currentTarget, event.currentTarget.querySelector("button"));
  });
  $("#cc-chat-refresh")?.addEventListener("click", async () => {
    try {
      await Promise.all([loadParties(), loadRooms($("#cc-chat-room-search")?.value || "")]);
      if (state.memberId && state.influencerId) await openConversation(state.memberId, state.influencerId, true);
      showToast("최신 정보를 불러왔습니다.");
    } catch (error) {
      showToast(error.message, true);
    }
  });

  Promise.all([loadParties(), loadRooms()])
    .then(schedulePoll)
    .catch((error) => showToast(error.message, true));
})();
