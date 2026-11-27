// Kvist browser bridge, tab side: a frame script in each tab's content process.
// It runs Kvist's page helpers in a sandbox of the page (the page shares the DOM with it,
// not its JavaScript) and turns input requests into the same trusted events a mouse and a
// keyboard produce (keys themselves go in at the browser window, see bridge.js). The browser side (bridge.js) sends it requests; it answers each one.
(function (frame) {
  "use strict";
  if (frame.__kvistBridge) return;
  frame.__kvistBridge = true;

  // A tab that moves to another process (a cross-site navigation) leaves this script behind in the
  // old one, still subscribed to the tab's messages; the browser side addresses calls by process.
  const pid = Services.appinfo.processID;
  const sandboxes = new WeakMap();
  let preload = null;
  let tip = null;

  // A tab that comes back to a process it once left finds this script still there beside a new one;
  // only the one whose page is here answers.
  const live = () => {
    try {
      return Boolean(frame.content && frame.content.document);
    } catch (error) {
      return false;
    }
  };

  // Only web pages: the browser's own pages (settings, add-ons, about:...) run with its privileges.
  const allowed = () => {
    const location = frame.content.location;
    if (location.protocol === "http:" || location.protocol === "https:") return true;
    return location.href === "about:blank" && !frame.content.document.nodePrincipal.isSystemPrincipal;
  };

  // One sandbox per document: the window object outlives a navigation to another site, and a
  // sandbox made for the old site's principal is refused by the new one.
  const sandbox = () => {
    const win = frame.content;
    const doc = win.document;
    let box = sandboxes.get(doc);
    if (!box) {
      box = Cu.Sandbox(win, { sandboxPrototype: win, wantXrays: true, sameZoneAs: win, sandboxName: "kvist" });
      sandboxes.set(doc, box);
    }
    return box;
  };

  const runPreload = () => {
    if (!preload || !allowed()) return;
    try {
      Cu.evalInSandbox(preload, sandbox());
    } catch (error) {
      Cu.reportError(`Kvist page helpers failed: ${error}`);
    }
  };

  // A new document in this tab: put the overlay back as early as the document exists.
  frame.addEventListener("DOMWindowCreated", event => {
    if (event.target.defaultView === frame.content) runPreload();
  }, true);

  const transaction = () => {
    if (!tip) tip = Cc["@mozilla.org/text-input-processor;1"].createInstance(Ci.nsITextInputProcessor);
    if (!tip.beginInputTransaction(frame.content, () => true)) throw new Error("another input method holds the keyboard");
    return tip;
  };


  const handlers = {
    async evaluate(data) {
      const result = Cu.evalInSandbox(data.expression, sandbox());
      return await result;
    },
    preload(data) {
      preload = data.source;
      if (preload && frame.content.document) runPreload();
      return null;
    },
    mouse(data) {
      // isDOMEventSynthesized=true keeps Firefox's own bookkeeping honest; pages still see isTrusted.
      frame.content.windowUtils.sendMouseEvent(data.type, data.x, data.y, data.button, data.clicks, 0,
        false, 0, 1, true, false, data.buttons);
      return null;
    },
    wheel(data) {
      frame.content.windowUtils.sendWheelEvent(data.x, data.y, data.dx, data.dy, 0, 0, 0, 0, 0, 0);
      return null;
    },
    text(data) {
      transaction().commitCompositionWith(data.text);
      return null;
    },
  };

  frame.addMessageListener("Kvist:call", async message => {
    const { id, kind, data } = message.data;
    if (message.data.pid !== pid || !live()) return;
    let reply;
    try {
      if (kind !== "preload" && !allowed()) {
        reply = { id, code: "page_refused", error: `this tab shows ${frame.content.location.href.slice(0, 80)}, the browser's own page; open a website in it first` };
      } else {
        reply = { id, value: await handlers[kind](data) };
      }
    } catch (error) {
      reply = { id, error: String(error && error.message || error).slice(0, 1000) };
    }
    frame.sendAsyncMessage("Kvist:reply", reply);
  });

  frame.sendAsyncMessage("Kvist:hello", { pid });
})(this);
