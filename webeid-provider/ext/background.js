"use strict";

const api = typeof browser !== "undefined" ? browser : chrome;
const HOST_NAME = "refineid_webeid";
const ALLOWED_ORIGIN = "https://tara.ria.ee";
const ERROR_CODE_FATAL = "ERR_WEBEID_NATIVE_FATAL";

function isAllowedSender(sender) {
  if (!sender || typeof sender.url !== "string") {
    return false;
  }
  try {
    return new URL(sender.url).origin === ALLOWED_ORIGIN;
  } catch (error) {
    void error;
    return false;
  }
}

api.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (
    !message ||
    (message.type !== "authenticate" && message.type !== "status")
  ) {
    return undefined;
  }
  if (!isAllowedSender(sender)) {
    sendResponse({
      error: {
        code: ERROR_CODE_FATAL,
        message: "origin is not allowed"
      }
    });
    return undefined;
  }

  let port;
  try {
    port = api.runtime.connectNative(HOST_NAME);
  } catch (error) {
    sendResponse({
      error: {
        code: ERROR_CODE_FATAL,
        message: (error && error.message) || "native host unavailable"
      }
    });
    return undefined;
  }

  let settled = false;

  function settle(reply) {
    if (settled) {
      return;
    }
    settled = true;
    sendResponse(reply);
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
    const error = api.runtime.lastError;
    settle({
      error: {
        code: ERROR_CODE_FATAL,
        message: error ? error.message : "native host disconnected"
      }
    });
  });

  if (message.type === "status") {
    port.postMessage({ action: "status" });
  } else {
    port.postMessage({
      action: "authenticate",
      challengeNonce: message.challengeNonce,
      origin: message.origin
    });
  }

  return true;
});
