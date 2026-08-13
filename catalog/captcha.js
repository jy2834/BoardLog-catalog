(() => {
  "use strict";

  const siteKey = document.querySelector('meta[name="boardlog-turnstile-site-key"]')?.content ?? "";
  const status = document.getElementById("status");

  function sendToAndroid(type, token = "") {
    const bridge = window.BoardLogTurnstile;
    if (bridge && typeof bridge.postToken === "function") {
      bridge.postToken(type === "success" ? token : "");
    }
  }

  window.boardLogTurnstileReady = () => {
    if (siteKey === "" || siteKey === "BOARDLOG_TURNSTILE_SITE_KEY") {
      status.textContent = "사람 확인 설정이 아직 완료되지 않았습니다.";
      sendToAndroid("configuration-error");
      return;
    }
    window.turnstile.render("#turnstile", {
      sitekey: siteKey,
      action: "boardlog_submit",
      callback: (token) => {
        status.textContent = "확인되었습니다.";
        sendToAndroid("success", token);
      },
      "expired-callback": () => {
        status.textContent = "확인 시간이 지나 다시 시도해 주세요.";
        sendToAndroid("expired");
      },
      "error-callback": () => {
        status.textContent = "확인에 실패했습니다. 다시 시도해 주세요.";
        sendToAndroid("error");
      },
    });
  };
})();
