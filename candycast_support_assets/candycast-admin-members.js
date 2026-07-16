(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const formatNumber = new Intl.NumberFormat("ko-KR");
  const state = {
    members: [],
    choices: null,
    influencers: [],
    selectedInfluencerId: "",
  };

  async function request(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
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

  function createInput(field, value, options = {}) {
    const input = document.createElement("input");
    input.dataset.field = field;
    input.value = value ?? "";
    input.type = options.type || "text";
    if (input.type !== "password" && input.value) input.title = input.value;
    if (options.placeholder) input.placeholder = options.placeholder;
    if (options.readonly) input.readOnly = true;
    if (options.min != null) input.min = String(options.min);
    if (options.max != null) input.max = String(options.max);
    if (options.maxLength) input.maxLength = options.maxLength;
    input.autocomplete = "off";
    return input;
  }

  function createSelect(field, value, options) {
    const select = document.createElement("select");
    select.dataset.field = field;
    for (const entry of options) {
      const option = document.createElement("option");
      if (typeof entry === "object") {
        option.value = entry.value;
        option.textContent = entry.label;
      } else {
        option.value = String(entry);
        option.textContent = typeof entry === "number" ? `${entry}등급` : String(entry);
      }
      option.selected = option.value === String(value);
      select.append(option);
    }
    return select;
  }

  function appendCell(row, control) {
    const cell = document.createElement("td");
    cell.append(control);
    row.append(cell);
  }

  function renderMembers() {
    const body = $("#cc-member-rows");
    if (!body || !state.choices) return;
    body.replaceChildren();
    if (!state.members.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 13;
      cell.className = "cc-admin-empty";
      cell.textContent = "등록된 회원이 없습니다.";
      row.append(cell);
      body.append(row);
      return;
    }

    for (const member of state.members) {
      const row = document.createElement("tr");
      row.dataset.originalId = member.id;
      row.classList.toggle("is-online", Boolean(member.online));
      appendCell(row, createInput("signupCode", member.signupCode, { readonly: true }));
      const idInput = createInput("id", member.id, { maxLength: 15 });
      if (member.online) {
        idInput.classList.add("cc-member-online-id");
        idInput.title = "현재 로그인 중";
        idInput.setAttribute("aria-label", `${member.id} 현재 로그인 중`);
      }
      appendCell(row, idInput);
      appendCell(row, createInput("password", "", { type: "password", placeholder: "새 비밀번호", maxLength: 15 }));
      appendCell(row, createInput("nickname", member.nickname, { maxLength: 20 }));
      appendCell(row, createInput("phone", member.phone, { maxLength: 20 }));
      appendCell(row, createInput("name", member.name, { maxLength: 40 }));
      appendCell(row, createSelect("role", member.role, state.choices.roles));
      appendCell(row, createSelect("displayGrade", member.displayGrade, state.choices.displayGrades));
      appendCell(row, createSelect("internalGrade", member.internalGrade, state.choices.internalGrades));
      appendCell(row, createInput("candy", member.candy, { type: "number", min: 0, max: 9999999999 }));
      appendCell(row, createSelect("balanceStatus", member.balanceStatus, state.choices.balanceStatuses));
      appendCell(row, createSelect("accountStatus", member.accountStatus, state.choices.accountStatuses));

      const actionCell = document.createElement("td");
      const actions = document.createElement("div");
      actions.className = "cc-member-actions";
      const save = document.createElement("button");
      save.type = "button";
      save.className = "cc-admin-primary";
      save.dataset.memberAction = "save";
      save.textContent = "저장";
      const gift = document.createElement("button");
      gift.type = "button";
      gift.className = "cc-admin-secondary";
      gift.dataset.memberAction = "gift";
      gift.textContent = "선물";
      actions.append(save, gift);
      actionCell.append(actions);
      row.append(actionCell);
      body.append(row);
    }
  }

  async function loadMembers(search = "") {
    const payload = await request(`/api/admin/members?q=${encodeURIComponent(search)}`);
    state.members = payload.members || [];
    state.choices = payload.choices || state.choices;
    renderMembers();
  }

  function collectMemberRow(row) {
    const value = (field) => row.querySelector(`[data-field="${field}"]`)?.value ?? "";
    return {
      originalId: row.dataset.originalId,
      id: value("id"),
      password: value("password"),
      nickname: value("nickname"),
      phone: value("phone"),
      name: value("name"),
      role: value("role"),
      displayGrade: value("displayGrade"),
      internalGrade: Number(value("internalGrade")),
      candy: Number(value("candy")),
      balanceStatus: value("balanceStatus"),
      accountStatus: value("accountStatus"),
    };
  }

  async function saveMember(row, button) {
    row.classList.add("is-saving");
    button.disabled = true;
    try {
      await request("/api/admin/members/update", {
        method: "POST",
        body: JSON.stringify(collectMemberRow(row)),
      });
      showToast("회원 정보가 저장되었습니다.");
      await loadMembers($("#cc-member-search")?.value || "");
    } catch (error) {
      showToast(error.message, true);
    } finally {
      row.classList.remove("is-saving");
      button.disabled = false;
    }
  }

  function renderCodes(payload) {
    const list = $("#cc-code-list");
    const summary = $("#cc-code-summary");
    if (!list || !summary) return;
    const codes = payload.codes || [];
    summary.textContent = `사용 가능 ${formatNumber.format(payload.available || 0)}개 / 전체 ${formatNumber.format(codes.length)}개`;
    list.replaceChildren();
    if (!codes.length) {
      const empty = document.createElement("p");
      empty.className = "cc-admin-empty";
      empty.textContent = "발급된 가입코드가 없습니다.";
      list.append(empty);
      return;
    }
    for (const code of codes) {
      const item = document.createElement("article");
      item.className = `cc-code-item${code.active ? "" : " is-disabled"}`;
      const content = document.createElement("div");
      const codeText = document.createElement("strong");
      codeText.textContent = code.code;
      const detail = document.createElement("small");
      const usage = `가입 ${formatNumber.format(code.useCount || 0)}명`;
      detail.textContent = `${code.label || `발급 ${code.createdAt}`} · ${usage}`;
      content.append(codeText, detail);
      const badge = document.createElement("span");
      badge.className = "cc-code-state";
      badge.textContent = code.active ? "사용가능" : "중지";
      item.append(content, badge);
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = code.active ? "cc-admin-danger" : "cc-admin-secondary";
      toggle.dataset.codeAction = "toggle";
      toggle.dataset.code = code.code;
      toggle.dataset.active = code.active ? "0" : "1";
      toggle.textContent = code.active ? "사용 중지" : "다시 사용";
      item.append(toggle);
      list.append(item);
    }
  }

  async function loadCodes() {
    renderCodes(await request("/api/admin/signup-codes"));
  }

  async function loadInfluencers() {
    const payload = await request("/api/admin/influencers");
    state.influencers = payload.influencers || [];
  }

  function renderGiftInfluencers(search = "") {
    const list = $("#cc-gift-bj-list");
    if (!list) return;
    const term = search.trim().toLocaleLowerCase("ko-KR");
    const filtered = state.influencers.filter((profile) => {
      const value = `${profile.id} ${profile.name} ${profile.nickname}`.toLocaleLowerCase("ko-KR");
      return !term || value.includes(term);
    }).slice(0, 80);
    list.replaceChildren();
    if (!filtered.length) {
      const empty = document.createElement("p");
      empty.className = "cc-admin-empty";
      empty.textContent = "검색된 BJ가 없습니다.";
      list.append(empty);
      return;
    }
    for (const profile of filtered) {
      const option = document.createElement("button");
      option.type = "button";
      option.className = `cc-gift-bj-option${state.selectedInfluencerId === profile.id ? " is-selected" : ""}`;
      option.dataset.influencerId = profile.id;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", state.selectedInfluencerId === profile.id ? "true" : "false");
      const image = document.createElement("img");
      image.src = profile.image || "/img/no_profile.gif";
      image.alt = "";
      image.loading = "lazy";
      const text = document.createElement("span");
      const name = document.createElement("strong");
      name.textContent = profile.name || profile.nickname || profile.id;
      const id = document.createElement("small");
      id.textContent = `${profile.nickname || profile.id} · ${profile.id}`;
      text.append(name, id);
      option.append(image, text);
      list.append(option);
    }
  }

  function openGift(row) {
    const modal = $("#cc-gift-modal");
    const form = $("#cc-gift-form");
    if (!modal || !form) return;
    const memberId = row.dataset.originalId;
    const nickname = row.querySelector('[data-field="nickname"]')?.value || memberId;
    form.reset();
    form.elements.memberId.value = memberId;
    form.elements.influencerId.value = "";
    state.selectedInfluencerId = "";
    $("#cc-gift-member").textContent = `${nickname} (${memberId}) 회원에게 전송`;
    $("#cc-gift-bj-search").value = "";
    renderGiftInfluencers();
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    $("#cc-gift-bj-search").focus();
  }

  function closeGift() {
    const modal = $("#cc-gift-modal");
    if (!modal) return;
    modal.hidden = true;
    document.body.style.overflow = "";
  }

  async function submitGift(form, button) {
    if (!state.selectedInfluencerId) {
      showToast("보내는 BJ를 선택해주세요.", true);
      return;
    }
    button.disabled = true;
    try {
      const payload = await request("/api/admin/gifts", {
        method: "POST",
        body: JSON.stringify({
          memberId: form.elements.memberId.value,
          influencerId: state.selectedInfluencerId,
          message: form.elements.message.value,
          amount: Number(form.elements.amount.value),
        }),
      });
      closeGift();
      showToast(`캔디 선물을 보냈습니다. 새 잔고 ${formatNumber.format(payload.balance)}개`);
      await loadMembers($("#cc-member-search")?.value || "");
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  document.addEventListener("click", async (event) => {
    const memberButton = event.target.closest("[data-member-action]");
    if (memberButton) {
      const row = memberButton.closest("tr");
      if (!row) return;
      if (memberButton.dataset.memberAction === "save") await saveMember(row, memberButton);
      if (memberButton.dataset.memberAction === "gift") openGift(row);
      return;
    }
    const codeButton = event.target.closest("[data-code-action='toggle']");
    if (codeButton) {
      codeButton.disabled = true;
      try {
        await request("/api/admin/signup-codes/toggle", {
          method: "POST",
          body: JSON.stringify({ code: codeButton.dataset.code, active: codeButton.dataset.active === "1" }),
        });
        await loadCodes();
      } catch (error) {
        showToast(error.message, true);
        codeButton.disabled = false;
      }
      return;
    }
    const bjOption = event.target.closest(".cc-gift-bj-option");
    if (bjOption) {
      state.selectedInfluencerId = bjOption.dataset.influencerId || "";
      $("#cc-gift-form").elements.influencerId.value = state.selectedInfluencerId;
      renderGiftInfluencers($("#cc-gift-bj-search").value);
      return;
    }
    if (event.target.closest("[data-gift-action='close']")) closeGift();
  });

  $("#cc-code-create")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const payload = await request("/api/admin/signup-codes/create", {
        method: "POST",
        body: JSON.stringify({ code: form.elements.code.value, label: form.elements.label.value }),
      });
      form.reset();
      showToast(`${payload.code} 가입코드를 발급했습니다.`);
      await loadCodes();
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  $("#cc-code-generate")?.addEventListener("click", () => {
    const bytes = new Uint8Array(5);
    crypto.getRandomValues(bytes);
    const suffix = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("").toUpperCase();
    $("#cc-code-create").elements.code.value = `CANDY${suffix}`;
  });

  $("#cc-gift-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    submitGift(event.currentTarget, event.currentTarget.querySelector('[type="submit"]'));
  });

  $("#cc-gift-bj-search")?.addEventListener("input", (event) => renderGiftInfluencers(event.target.value));
  $("#cc-member-search")?.addEventListener("input", debounce((event) => {
    loadMembers(event.target.value).catch((error) => showToast(error.message, true));
  }));
  $("#cc-members-refresh")?.addEventListener("click", () => {
    Promise.all([loadMembers($("#cc-member-search")?.value || ""), loadInfluencers()])
      .then(() => showToast("최신 정보를 불러왔습니다."))
      .catch((error) => showToast(error.message, true));
  });
  $("#cc-partners-refresh")?.addEventListener("click", () => {
    loadCodes()
      .then(() => showToast("최신 정보를 불러왔습니다."))
      .catch((error) => showToast(error.message, true));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#cc-gift-modal")?.hidden) closeGift();
  });

  const initialTasks = [];
  if ($("#cc-member-rows")) initialTasks.push(loadMembers(), loadInfluencers());
  if ($("#cc-code-list")) initialTasks.push(loadCodes());
  Promise.all(initialTasks).catch((error) => showToast(error.message, true));
})();
