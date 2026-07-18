(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const state = {
    profiles: [],
    selectedId: "",
    mainImageData: "",
    profileImageData: "",
    loadingImage: false,
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

  function imageFor(profile, kind = "profile") {
    if (!profile) return "/img/no_profile.gif";
    if (kind === "main") return profile.mainImage || profile.image || profile.profileImage || "/img/no_profile.gif";
    return profile.profileImage || profile.image || profile.mainImage || "/img/no_profile.gif";
  }

  function currentProfile() {
    return state.profiles.find((profile) => profile.id === state.selectedId) || null;
  }

  function renderList() {
    const list = $("#cc-bj-list");
    if (!list) return;
    list.replaceChildren();
    const term = ($("#cc-bj-search")?.value || "").trim().toLocaleLowerCase("ko-KR");
    const filtered = state.profiles.filter((profile) => {
      if (!term) return true;
      return [profile.id, profile.name, profile.nickname, profile.theme]
        .join(" ")
        .toLocaleLowerCase("ko-KR")
        .includes(term);
    });
    if (!filtered.length) {
      const empty = document.createElement("p");
      empty.className = "cc-admin-empty";
      empty.textContent = "표시할 BJ가 없습니다.";
      list.append(empty);
      return;
    }

    for (const profile of filtered) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `cc-bj-list-item${profile.id === state.selectedId ? " is-active" : ""}`;
      button.dataset.id = profile.id;

      const image = document.createElement("img");
      image.src = imageFor(profile);
      image.alt = "";
      image.loading = "lazy";

      const body = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = profile.name || profile.nickname || profile.id;
      const meta = document.createElement("small");
      meta.textContent = [profile.nickname, profile.id].filter(Boolean).join(" · ");
      const theme = document.createElement("em");
      theme.textContent = [profile.theme, profile.viewerCount].filter(Boolean).join(" · ");
      body.append(title, meta, theme);

      button.append(image, body);
      list.append(button);
    }
  }

  function setFormEnabled(enabled) {
    const form = $("#cc-bj-form");
    if (!form) return;
    form.querySelectorAll("input, button").forEach((control) => {
      if (control.name === "id") return;
      control.disabled = !enabled;
    });
    const submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = !enabled || state.loadingImage;
  }

  function fillForm(profile) {
    const form = $("#cc-bj-form");
    if (!form) return;
    form.elements.id.value = profile?.id || "";
    form.elements.title.value = profile?.name || "";
    form.elements.nickname.value = profile?.nickname || "";
    form.elements.theme.value = profile?.theme || "";
    form.elements.viewerCount.value = profile?.viewerCount || "";
    $("#cc-bj-main-preview").src = imageFor(profile, "main");
    $("#cc-bj-profile-preview").src = imageFor(profile, "profile");
    state.mainImageData = "";
    state.profileImageData = "";
    const mainFile = $("#cc-bj-main-image");
    const profileFile = $("#cc-bj-profile-image");
    if (mainFile) mainFile.value = "";
    if (profileFile) profileFile.value = "";
    setFormEnabled(Boolean(profile));
  }

  function selectProfile(id) {
    state.selectedId = id;
    renderList();
    fillForm(currentProfile());
  }

  async function loadProfiles(keepSelection = true) {
    const search = $("#cc-bj-search")?.value || "";
    const payload = await request(`/api/admin/influencer-profiles?q=${encodeURIComponent(search)}`);
    state.profiles = payload.influencers || [];
    if (!keepSelection || !state.profiles.some((profile) => profile.id === state.selectedId)) {
      state.selectedId = state.profiles[0]?.id || "";
    }
    renderList();
    fillForm(currentProfile());
  }

  async function fileToDataUrl(file) {
    if (!file) return "";
    if (window.CandyCastImage?.compress) {
      const compressed = await window.CandyCastImage.compress(file);
      return compressed.data;
    }
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(new Error("이미지를 읽을 수 없습니다."));
      reader.readAsDataURL(file);
    });
  }

  async function handleImageChange(input, previewSelector, targetKey) {
    const file = input.files?.[0];
    if (!file) return;
    try {
      state.loadingImage = true;
      setFormEnabled(Boolean(currentProfile()));
      const dataUrl = await fileToDataUrl(file);
      state[targetKey] = dataUrl;
      const preview = $(previewSelector);
      if (preview) preview.src = dataUrl;
    } catch (error) {
      input.value = "";
      showToast(error.message || "이미지를 처리하지 못했습니다.", true);
    } finally {
      state.loadingImage = false;
      setFormEnabled(Boolean(currentProfile()));
    }
  }

  async function saveProfile(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const id = form.elements.id.value;
    if (!id) return;
    const submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = true;
    try {
      const payload = {
        id,
        title: form.elements.title.value,
        nickname: form.elements.nickname.value,
        theme: form.elements.theme.value,
        viewerCount: form.elements.viewerCount.value,
      };
      if (state.mainImageData) payload.mainImage = state.mainImageData;
      if (state.profileImageData) payload.profileImage = state.profileImageData;
      const result = await request("/api/admin/influencer-profiles/update", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const profile = result.profile || {};
      state.profiles = state.profiles.map((entry) => (
        entry.id === profile.id ? { ...entry, ...profile } : entry
      ));
      state.mainImageData = "";
      state.profileImageData = "";
      renderList();
      fillForm(currentProfile());
      showToast("BJ 정보가 저장되었습니다.");
    } catch (error) {
      showToast(error.message || "저장하지 못했습니다.", true);
      setFormEnabled(Boolean(currentProfile()));
    }
  }

  function bindEvents() {
    $("#cc-bj-refresh")?.addEventListener("click", () => {
      loadProfiles(true).catch((error) => showToast(error.message, true));
    });
    $("#cc-bj-search")?.addEventListener("input", debounce(() => renderList()));
    $("#cc-bj-list")?.addEventListener("click", (event) => {
      const button = event.target.closest(".cc-bj-list-item");
      if (!button) return;
      selectProfile(button.dataset.id || "");
    });
    $("#cc-bj-main-image")?.addEventListener("change", (event) => {
      handleImageChange(event.currentTarget, "#cc-bj-main-preview", "mainImageData");
    });
    $("#cc-bj-profile-image")?.addEventListener("change", (event) => {
      handleImageChange(event.currentTarget, "#cc-bj-profile-preview", "profileImageData");
    });
    $("#cc-bj-form")?.addEventListener("submit", saveProfile);
  }

  bindEvents();
  setFormEnabled(false);
  loadProfiles(false).catch((error) => showToast(error.message, true));
})();
