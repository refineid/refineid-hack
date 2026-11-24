(function () {
  "use strict";

  const ERROR_CODE_FATAL = "ERR_WEBEID_NATIVE_FATAL";

  function post(payload) {
    window.postMessage(payload, window.location.origin);
  }

  function fail(action, code, message) {
    post({
      action: action,
      error: {
        code: code || ERROR_CODE_FATAL,
        message: String(message)
      }
    });
  }

  function sendRuntimeMessage(payload) {
    if (
      typeof browser !== "undefined" &&
      browser.runtime &&
      typeof browser.runtime.sendMessage === "function"
    ) {
      return browser.runtime.sendMessage(payload);
    }
    return new Promise(function (resolve, reject) {
      chrome.runtime.sendMessage(payload, function (response) {
        const error = chrome.runtime.lastError;
        if (error) {
          reject(new Error(error.message));
          return;
        }
        resolve(response);
      });
    });
  }

  window.addEventListener(
    "message",
    function (event) {
      if (event.source !== window) {
        return;
      }
      const data = event.data;
      if (!data || typeof data.action !== "string") {
        return;
      }

      if (data.action === "web-eid:status") {
        post({ action: "web-eid:status-ack" });
        sendRuntimeMessage({ type: "status" })
          .then(function (reply) {
            if (!reply || reply.error) {
              fail(
                "web-eid:status-failure",
                reply && reply.error && reply.error.code,
                reply && reply.error ? reply.error.message : "native host failed"
              );
              return;
            }
            post(Object.assign({ action: "web-eid:status-success" }, reply));
          })
          .catch(function (error) {
            fail(
              "web-eid:status-failure",
              ERROR_CODE_FATAL,
              (error && error.message) || error
            );
          });
        return;
      }

      if (data.action !== "web-eid:authenticate") {
        return;
      }

      post({ action: "web-eid:authenticate-ack" });

      sendRuntimeMessage({
        type: "authenticate",
        challengeNonce: data.challengeNonce,
        origin: window.location.origin
      })
        .then(function (token) {
          if (!token || token.error) {
            fail(
              "web-eid:authenticate-failure",
              token && token.error && token.error.code,
              token && token.error ? token.error.message : "native host failed"
            );
            return;
          }
          post(
            Object.assign({ action: "web-eid:authenticate-success" }, token)
          );
        })
        .catch(function (error) {
          fail(
            "web-eid:authenticate-failure",
            ERROR_CODE_FATAL,
            (error && error.message) || error
          );
        });
    },
    false
  );
})();
