(function () {
  "use strict";

  function ready(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, { once: true });
    } else {
      callback();
    }
  }

  async function parseResponse(response) {
    var payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      payload = {};
    }
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || "요청을 처리하지 못했습니다.");
    }
    return payload;
  }

  ready(function () {
    var input = document.getElementById("iconimg");
    var save = document.getElementById("upfile");
    var remove = document.getElementById("cc-profile-delete");
    var preview = document.querySelector(".cc-member-profile-preview");
    var popup = document.querySelector(".file-popup");
    var previewUrl = "";

    if (!input || !save) return;

    if (window.jQuery) {
      window.jQuery(save).off("click");
    }

    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file || !preview) return;
      if (!/^image\/(jpeg|png)$/i.test(file.type || "")) {
        input.value = "";
        window.alert("JPG 또는 PNG 이미지만 선택할 수 있습니다.");
        return;
      }
      if (file.size > 50 * 1024 * 1024) {
        input.value = "";
        window.alert("원본 이미지는 50MB 이하만 선택할 수 있습니다.");
        return;
      }
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(file);
      preview.src = previewUrl;
    });

    save.addEventListener("click", async function (event) {
      event.preventDefault();
      if (save.getAttribute("aria-disabled") === "true") return;
      var file = input.files && input.files[0];
      if (!file) {
        window.alert("저장할 프로필 이미지를 선택해주세요.");
        return;
      }
      if (!window.CandyCastImage || typeof window.CandyCastImage.compress !== "function") {
        window.alert("이미지 압축 기능을 불러오지 못했습니다. 새로고침 후 다시 시도해주세요.");
        return;
      }

      var originalText = save.textContent;
      save.setAttribute("aria-disabled", "true");
      save.textContent = "저장 중...";
      try {
        var compressed = await window.CandyCastImage.compress(file);
        var response = await fetch("/api/member/profile", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ image: compressed.data, name: compressed.name })
        });
        var payload = await parseResponse(response);
        window.alert(payload.message || "프로필 이미지가 저장되었습니다.");
        window.location.reload();
      } catch (error) {
        window.alert(error && error.message ? error.message : "프로필 이미지를 저장하지 못했습니다.");
      } finally {
        save.removeAttribute("aria-disabled");
        save.textContent = originalText;
      }
    });

    if (remove) {
      if (window.jQuery) window.jQuery(remove).off("click");
      remove.addEventListener("click", async function (event) {
        event.preventDefault();
        if (!window.confirm("등록한 프로필 이미지를 삭제할까요?")) return;
        try {
          var response = await fetch("/api/member/profile/delete", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: "{}"
          });
          var payload = await parseResponse(response);
          window.alert(payload.message || "프로필 이미지가 삭제되었습니다.");
          window.location.reload();
        } catch (error) {
          window.alert(error && error.message ? error.message : "프로필 이미지를 삭제하지 못했습니다.");
        }
      });
    }

    window.addEventListener("beforeunload", function () {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    });

    if (popup) popup.setAttribute("aria-label", "프로필 이미지 등록");
  });
})();
