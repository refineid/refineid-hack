(function () {
  "use strict";

  const EXTENSION_VERSION = "1.0.0";
  const APP_VERSION = "refineid/1.0.0";
  const ERROR_CODE = "ERR_WEBEID_NATIVE_FATAL";

  function post(payload) {
    window.postMessage(payload, "*");
  }

  function fail(message) {
    post({
      action: "web-eid:authenticate-failure",
      error: { code: ERROR_CODE, message: String(message) }
    });
  }

  window.addEventListener("message", function (event) {
    if (event.source !== window) {
      return;
    }
    const data = event.data;
    if (!data || typeof data.action !== "string") {
      return;
    }

    if (data.action === "web-eid:status") {
      post({ action: "web-eid:status-ack" });
      post({
        action: "web-eid:status-success",
        extension: EXTENSION_VERSION,
        nativeApp: APP_VERSION
      });
      return;
    }

    if (data.action !== "web-eid:authenticate") {
      return;
    }

    post({ action: "web-eid:authenticate-ack" });

    browser.runtime
      .sendMessage({
        type: "authenticate",
        challengeNonce: data.challengeNonce,
        origin: window.location.origin
      })
      .then(function (token) {
        if (!token || token.error) {
          fail(token && token.error ? token.error.message : "native host failed");
          return;
        }
        post(
          Object.assign({ action: "web-eid:authenticate-success" }, token)
        );
      })
      .catch(function (error) {
        fail((error && error.message) || error);
      });
  }, false);
})();
