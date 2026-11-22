"use strict";

const HOST_NAME = "refineid_webeid";

browser.runtime.onMessage.addListener(function (message) {
  if (!message || message.type !== "authenticate") {
    return undefined;
  }

  return new Promise(function (resolve) {
    const port = browser.runtime.connectNative(HOST_NAME);
    let settled = false;

    function settle(reply) {
      if (settled) {
        return;
      }
      settled = true;
      resolve(reply);
      try {
        port.disconnect();
      } catch (error) {
        void error;
      }
    }

    port.onMessage.addListener(function (reply) {
      settle(reply);
    });

    port.onDisconnect.addListener(function () {
      const error = browser.runtime.lastError;
      settle({
        error: {
          code: "ERR_WEBEID_NATIVE_FATAL",
          message: error ? error.message : "native host disconnected"
        }
      });
    });

    port.postMessage({
      action: "authenticate",
      challengeNonce: message.challengeNonce,
      origin: message.origin
    });
  });
});
