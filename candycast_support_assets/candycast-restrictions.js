(() => {
  "use strict";

  const body = document.body;
  if (!body || body.dataset.ccAuthenticated !== "1") return;

  const messages = {
    account: "회원님의 계정에서 비정상적인 이용 내역이 확인되어 계정이 즉시 이용정지 처리되었습니다. 현재 고객센터를 제외한 모든 서비스 이용이 제한된 상태입니다.\n자세한 사항은 고객센터로 문의해 주시기 바랍니다.",
    balance: "전자금융거래법 및 특정금융정보법(자금세탁방지 의무)에 따라 비정상적인 거래 내역이 감지되어 회원님의 캔디 잔고가 동결 처리되었습니다. 현재 고객센터를 통한 본인 확인 및 소명 절차 진행 후에만 해제가 가능합니다.\n자세한 사항은 고객센터로 문의해 주시기 바랍니다.",
  };

  const accountFrozen = body.dataset.accountStatus === "계정동결";
  const balanceFrozen = body.dataset.balanceStatus === "잔고동결";
  const userKey = body.dataset.ccUser || "member";

  function showRestriction(type) {
    const message = messages[type];
    if (!message) return;
    let layer = document.querySelector("#cc-restriction-layer");
    if (!layer) {
      layer = document.createElement("div");
      layer.id = "cc-restriction-layer";
      layer.className = "cc-restriction-layer";
      layer.innerHTML = '<section class="cc-restriction-modal" role="dialog" aria-modal="true" aria-labelledby="cc-restriction-title"><h2 id="cc-restriction-title">이용 제한 안내</h2><p></p><button type="button">확인</button></section>';
      document.body.append(layer);
      layer.querySelector("button").addEventListener("click", () => { layer.hidden = true; });
      layer.addEventListener("click", (event) => {
        if (event.target === layer) layer.hidden = true;
      });
    }
    layer.querySelector("p").textContent = message;
    layer.hidden = false;
    layer.querySelector("button").focus();
  }

  function showOnce(type) {
    const key = `cc-restriction-${type}-${userKey}`;
    if (sessionStorage.getItem(key) === "1") return;
    sessionStorage.setItem(key, "1");
    showRestriction(type);
  }

  if (!accountFrozen) sessionStorage.removeItem(`cc-restriction-account-${userKey}`);
  if (!balanceFrozen) sessionStorage.removeItem(`cc-restriction-balance-${userKey}`);
  if (accountFrozen) showOnce("account");
  else if (balanceFrozen) showOnce("balance");

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.closest("#cc-support-root")) return;
    const action = form.getAttribute("action") || "";
    if (accountFrozen) {
      event.preventDefault();
      showRestriction("account");
      return;
    }
    if (balanceFrozen && /formdata\.php|export|import/i.test(action)) {
      event.preventDefault();
      showRestriction("balance");
    }
  }, true);

  document.addEventListener("click", (event) => {
    const target = event.target.closest("a,button");
    if (!target || target.closest("#cc-support-root") || target.closest("#cc-restriction-layer")) return;
    if (target.matches('a[href="/bbs/logout.php"]')) return;
    const label = target.textContent.replace(/\s+/g, "").trim();
    const href = target.getAttribute("href") || "";
    if (accountFrozen && (/javascript:/i.test(href) || target.tagName === "BUTTON")) {
      event.preventDefault();
      event.stopImmediatePropagation();
      showRestriction("account");
      return;
    }
    if (balanceFrozen && (/환전|구매하기|캔디구매/.test(label) || /export|formdata/i.test(href))) {
      event.preventDefault();
      event.stopImmediatePropagation();
      showRestriction("balance");
    }
  }, true);
})();
