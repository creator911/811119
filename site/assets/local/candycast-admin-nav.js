(function () {
  "use strict";
  var badge = document.getElementById("cc-admin-support-unread");
  if (!badge || document.querySelector(".cc-admin-support")) return;
  var timer = 0;
  function refresh() {
    return fetch("/api/admin/support/unread", { headers: { "Accept": "application/json" } })
      .then(function (response) { return response.ok ? response.json() : Promise.reject(); })
      .then(function (body) {
        var count = Math.max(0, Number(body.unread) || 0);
        badge.textContent = count > 99 ? "99+" : String(count);
        badge.hidden = count === 0;
      })
      .catch(function () {});
  }
  function schedule() {
    if (timer) window.clearTimeout(timer);
    timer = 0;
    if (document.hidden) return;
    timer = window.setTimeout(function () {
      timer = 0;
      refresh().finally(schedule);
    }, 5000 + Math.floor(Math.random() * 500));
  }
  refresh().finally(schedule);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (timer) window.clearTimeout(timer);
      timer = 0;
      return;
    }
    refresh().finally(schedule);
  });
})();
