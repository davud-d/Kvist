import Foundation

/// One request from the page: `KvistHost.call(id, name, json)`.
public struct BridgeCall {
    public let id: String
    public let name: String
    public let args: [String: Any]
}

/// The words the page (cloud/mobile/js/native.js) and the app say to each other. The page posts
/// {id, name, json} to the message handler `kvist`; the handler's reply comes back through the promise
/// `postMessage` returns, and the start script hands it to `window.__kvistReply(id, payload)`.
public enum Bridge {
    public static let handlerName = "kvist"

    /// Runs in the page before its own scripts: makes `window.KvistHost` out of the message handler. `store`: the App
    /// Store build, in which the page sells nothing (cloud/mobile/js/native.js, storeBuild).
    public static func startScript(store: Bool) -> String { """
    (function () {
      if (window.KvistHost) return;
      var handlers = window.webkit && window.webkit.messageHandlers;
      if (!handlers || !handlers.kvist) return;
      window.KvistHost = {
        platform: "ios",
        store: \(store),
        call: function (id, name, json) {
          var answer = function (payload) {
            if (window.__kvistReply) window.__kvistReply(id, payload === undefined ? null : payload);
          };
          handlers.kvist.postMessage({ id: id, name: name, json: json }).then(answer, function () { answer(null); });
        }
      };
    })();
    """ }

    /// The body of a script message, or nil when it is not a call.
    public static func parse(_ body: Any) -> BridgeCall? {
        guard let outer = body as? [String: Any],
              let id = outer["id"] as? String, !id.isEmpty,
              let name = outer["name"] as? String, !name.isEmpty else { return nil }
        // `json` is the page's own JSON.stringify of the arguments; a page that sent none sent "{}".
        let text = (outer["json"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "{}"
        guard let args = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any] else { return nil }
        return BridgeCall(id: id, name: name, args: args)
    }

    public static func token(_ token: String) -> [String: Any] { ["token": token] }
    public static func cancelled() -> [String: Any] { ["cancelled": true] }
    public static func failed(_ code: String) -> [String: Any] { ["error": code] }
}
