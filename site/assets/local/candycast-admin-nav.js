(function () {
  "use strict";
  var badges = [
    {
      element: document.getElementById("cc-admin-chat-unread"),
      endpoint: "/api/admin/member-chat/unread"
    },
    {
      element: document.getElementById("cc-admin-support-unread"),
      endpoint: "/api/admin/support/unread"
    }
  ].filter(function (item) { return item.element; });
  if (!badges.length) return;
  var timer = 0;
  function updateBadge(badge, count) {
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.hidden = count === 0;
  }
  function refresh() {
    return Promise.all(badges.map(function (item) {
      return fetch(item.endpoint, {
        credentials: "same-origin",
        headers: { "Accept": "application/json" }
      }).then(function (response) {
        return response.ok ? response.json() : Promise.reject();
      }).then(function (body) {
        updateBadge(item.element, Math.max(0, Number(body.unread) || 0));
      }).catch(function () {});
    }));
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
