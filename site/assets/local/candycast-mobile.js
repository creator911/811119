(function () {
  "use strict";

  if (window.__candycastMobileBound) return;
  window.__candycastMobileBound = true;

  function pathMatchesRoute(route, path) {
    if (route === "home") return path === "/" || path === "/index.html" || path === "/live.php";
    if (route === "chat") return path === "/chatlist.php" || path.indexOf("/chat/") === 0;
    if (route === "ranking") return path === "/toplank.php";
    if (route === "shop") return path === "/flex.php";
    if (route === "my") return path === "/my.php" || path === "/my2.php";
    return false;
  }

  function setActiveNavigation() {
    var path = window.location.pathname;
    document.querySelectorAll(".cc-mobile-nav [data-mobile-route]").forEach(function (link) {
      var active = pathMatchesRoute(link.getAttribute("data-mobile-route"), path);
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }

  function setViewportHeight() {
    var viewport = window.visualViewport;
    var height = viewport ? viewport.height : window.innerHeight;
    document.documentElement.style.setProperty("--cc-viewport-height", height + "px");
  }

  function replaceBrokenProfile(image) {
    if (!image || image.dataset.ccFallbackApplied === "1") return;
    if (/\/img\/no_profile\.gif(?:\?|$)/.test(image.getAttribute("src") || "")) return;
    image.dataset.ccFallbackApplied = "1";
    image.src = "/img/no_profile.gif";
  }

  function protectProfileImages() {
    document.querySelectorAll('.zxcmnv img, img[alt="profile_image"]').forEach(function (image) {
      image.addEventListener("error", function () { replaceBrokenProfile(image); }, { once: true });
      if (image.complete && image.naturalWidth === 0) replaceBrokenProfile(image);
    });
  }

  function chatTarget(entry) {
    var onclick = entry.getAttribute("onclick") || "";
    var match = onclick.match(/location\.href\s*=\s*['\"]([^'\"]+)/);
    return match ? match[1] : "";
  }

  function makeChatCardsInteractive(cards) {
    cards.forEach(function (card) {
      var entry = card.querySelector(".woiej");
      var href = entry ? chatTarget(entry) : "";
      if (!href) return;
      card.classList.add("cc-chat-card");
      card.setAttribute("role", "link");
      card.setAttribute("tabindex", "0");
      card.dataset.href = href;
      card.addEventListener("click", function (event) {
        if (event.target.closest("a, button, input, select, textarea, .woiej")) return;
        window.location.href = href;
      });
      card.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          window.location.href = href;
        }
      });
    });
  }

  function setupChatSearch() {
    var list = document.querySelector(".cc-page-chatlist .maindddd");
    if (!list || document.querySelector(".cc-mobile-chat-search")) return;

    var cards = Array.prototype.filter.call(list.children, function (node) {
      return node.nodeType === 1;
    });
    makeChatCardsInteractive(cards);

    var form = document.createElement("form");
    form.className = "cc-mobile-chat-search";
    form.setAttribute("role", "search");
    form.innerHTML = '<label for="cc-chat-search">채팅 검색</label>' +
      '<div><input id="cc-chat-search" type="search" inputmode="search" autocomplete="off" ' +
      'placeholder="닉네임 또는 방송명 검색"><button type="button" aria-label="검색어 지우기" hidden>&times;</button></div>' +
      '<p aria-live="polite"></p>';
    list.parentNode.insertBefore(form, list);

    var input = form.querySelector("input");
    var clear = form.querySelector("button");
    var status = form.querySelector("p");

    function filterCards() {
      var query = input.value.trim().toLocaleLowerCase("ko-KR");
      var visible = 0;
      cards.forEach(function (card) {
        var matches = !query || card.textContent.toLocaleLowerCase("ko-KR").indexOf(query) !== -1;
        card.hidden = !matches;
        if (matches) visible += 1;
      });
      clear.hidden = !query;
      status.textContent = query ? visible + "개의 채팅방을 찾았습니다." : "전체 " + cards.length + "개 채팅방";
      list.classList.toggle("is-empty", visible === 0);
    }

    form.addEventListener("submit", function (event) { event.preventDefault(); });
    input.addEventListener("input", filterCards);
    clear.addEventListener("click", function () {
      input.value = "";
      filterCards();
      input.focus();
    });
    filterCards();
  }

  function setupMemberActions() {
    document.querySelectorAll("[data-cc-mobile-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-cc-mobile-action");
        if (action === "exchange") {
          if (typeof window.upexport === "function") window.upexport();
          return;
        }
        if (action === "support") {
          var root = document.getElementById("cc-support-root");
          var toggle = root && root.querySelector('[data-cc-support-action="toggle"]');
          if (toggle && root.dataset.open !== "true") toggle.click();
          return;
        }
      });
    });
  }

  function normalizeMyLinks() {
    var profile = document.querySelector(".my-tab .my-tab1");
    var history = document.querySelector(".my-tab .my-tab2");
    if (profile) profile.href = "/my.php";
    if (history) history.href = "/my2.php";
  }

  function labelRankingCells() {
    var labels = ["순위", "방송국", "시청자", "인기점수"];
    document.querySelectorAll(".cc-page-ranking .flx-table tbody tr").forEach(function (row) {
      Array.prototype.forEach.call(row.children, function (cell, index) {
        if (labels[index]) cell.setAttribute("data-label", labels[index]);
      });
    });
  }

  function setupRegistrationSubmit() {
    var form = document.querySelector('form[name="fregisterform"]');
    var submit = form && form.querySelector('#btn_submit');
    if (!form || !submit || form.dataset.ccSubmitBound === "1") return;
    form.dataset.ccSubmitBound = "1";

    async function sendRegistration(event) {
      if (event) event.preventDefault();
      if (form.dataset.ccSubmitting === "1") return;
      if (typeof form.checkValidity === "function" && !form.checkValidity()) {
        if (typeof form.reportValidity === "function") form.reportValidity();
        return;
      }

      form.dataset.ccSubmitting = "1";
      submit.disabled = true;
      try {
        var payload = new URLSearchParams();
        new FormData(form).forEach(function (value, key) {
          if (typeof value === "string") payload.append(key, value);
        });
        var response = await fetch(form.action, {
          method: "POST",
          body: payload,
          credentials: "same-origin",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" }
        });
        window.location.assign(response.url || "/bbs/login.php?registered=1");
      } catch (_error) {
        form.dataset.ccSubmitting = "0";
        submit.disabled = false;
        window.alert("회원가입 처리 중 오류가 발생했습니다. 다시 시도해주세요.");
      }
    }

    form.addEventListener("submit", sendRegistration);
    submit.addEventListener("click", sendRegistration);
  }

  function setupHeroCarousel() {
    var hero = document.querySelector(".cc-page-home .vedios");
    if (!hero || hero.dataset.ccHeroBound === "1") return;
    hero.dataset.ccHeroBound = "1";

    function getSwiper() {
      return hero.swiper && !hero.swiper.destroyed ? hero.swiper : null;
    }

    function move(direction) {
      var swiper = getSwiper();
      if (swiper) {
        if (direction < 0) swiper.slidePrev();
        else swiper.slideNext();
        return;
      }

      var selector = direction < 0 ? ".swiper-button-prev" : ".swiper-button-next";
      var control = hero.querySelector(selector);
      if (control) control.click();
    }

    function sideDirection(slide) {
      var active = hero.querySelector(".swiper-slide-active");
      if (!active || !slide || slide === active) return 0;

      var slideRect = slide.getBoundingClientRect();
      var activeRect = active.getBoundingClientRect();
      var slideCenter = slideRect.left + slideRect.width / 2;
      var activeCenter = activeRect.left + activeRect.width / 2;
      return slideCenter < activeCenter ? -1 : 1;
    }

    function handleSideActivation(event) {
      var link = event.target.closest(".swiper-slide > a");
      if (!link || !hero.contains(link)) return;

      var direction = sideDirection(link.closest(".swiper-slide"));
      if (!direction) return;

      event.preventDefault();
      event.stopImmediatePropagation();
      move(direction);
    }

    hero.addEventListener("click", handleSideActivation, true);
    hero.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      handleSideActivation(event);
    }, true);

    function enableTouch() {
      var swiper = getSwiper();
      if (!swiper) return false;
      swiper.allowTouchMove = true;
      swiper.params.allowTouchMove = true;
      swiper.params.simulateTouch = true;
      swiper.params.touchRatio = 1;
      swiper.update();
      return true;
    }

    if (!enableTouch()) {
      window.addEventListener("load", enableTouch, { once: true });
    }
  }

  function init() {
    setViewportHeight();
    setActiveNavigation();
    protectProfileImages();
    setupChatSearch();
    setupMemberActions();
    normalizeMyLinks();
    labelRankingCells();
    setupRegistrationSubmit();
    setupHeroCarousel();
    document.documentElement.classList.add("cc-mobile-ready");
  }

  window.addEventListener("resize", setViewportHeight, { passive: true });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", setViewportHeight, { passive: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
