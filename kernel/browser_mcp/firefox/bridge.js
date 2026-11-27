// Kvist browser bridge, browser side. kvist-autoconfig.cfg loads it at start-up with the
// browser's own privileges, from ~/.kvist/browser-bridge (kvist --browser-bridge install).
//
// Why it exists: remote debugging (--remote-debugging-port) makes Firefox tell every site
// navigator.webdriver = true, and Cloudflare, YouTube and others then treat the person as a
// robot even when Kvist does nothing. The bridge needs no remote agent: it listens on a Unix
// socket only this user can open, lists the tabs from the tab strip itself (unloaded ones too),
// and hands page work to a frame script in each tab (bridge-frame.js).
//
// Wire format: one JSON object per line. Requests {id, method, params}; replies {id, result}
// or {id, error, code}. A client that disconnects leaves nothing behind: its page scripts are
// removed, keys and buttons it held are released and the overlay leaves the page.
(function (scope) {
  "use strict";
  const { setTimeout, clearTimeout } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
  const { FileUtils } = ChromeUtils.importESModule("resource://gre/modules/FileUtils.sys.mjs");
  // The autoconfig global is bare: no TextEncoder/TextDecoder unless asked for.
  Cu.importGlobalProperties(["TextEncoder", "TextDecoder"]);

  const VERSION = 1;
  // sourceURL names the script in errors instead of the whole data: address.
  const FRAME_URL = "data:application/javascript;charset=utf-8," +
    encodeURIComponent(scope.FRAME_SOURCE + "\n//# sourceURL=kvist-bridge-frame.js\n");
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  // --- tabs ----------------------------------------------------------------------------------

  const tabIds = new WeakMap();
  const tabRefs = new Map();
  let nextTab = 0;

  const idOf = tab => {
    let id = tabIds.get(tab);
    if (!id) {
      id = "w" + (++nextTab);
      tabIds.set(tab, id);
    }
    tabRefs.set(id, new WeakRef(tab));
    return id;
  };

  const tabOf = id => {
    const tab = tabRefs.get(id)?.deref();
    if (!tab || tab.closing || !tab.isConnected) throw new BridgeError(`no tab ${id}: it was closed`);
    return tab;
  };

  const windows = () => [...Services.wm.getEnumerator("navigator:browser")].filter(win => !win.closed && win.gBrowser);
  const frontWindow = () => {
    const win = Services.wm.getMostRecentWindow("navigator:browser");
    if (!win) throw new BridgeError("the browser has no window open");
    return win;
  };

  class BridgeError extends Error {
    constructor(message, code = "") {
      super(message);
      this.code = code;
    }
  }

  // --- the frame scripts -------------------------------------------------------------------

  const keyboards = new WeakMap();  // window -> its text input processor
  const ready = new WeakMap();  // browser -> processes whose frame script said hello
  const waiting = new Map();
  const preloads = new Map();  // tab id -> {source, client}
  let nextCall = 0;

  Services.mm.loadFrameScript(FRAME_URL, true);
  Services.mm.addMessageListener("Kvist:hello", {
    receiveMessage(message) {
      const browser = message.target;
      const pid = message.data.pid;
      if (!ready.has(browser)) ready.set(browser, new Set());
      ready.get(browser).add(pid);
      const tab = browser.ownerGlobal?.gBrowser?.getTabForBrowser(browser);
      const entry = tab && preloads.get(idOf(tab));
      if (entry) browser.messageManager.sendAsyncMessage("Kvist:call", { id: 0, pid, kind: "preload", data: { source: entry.source } });
    },
  });
  Services.mm.addMessageListener("Kvist:reply", {
    receiveMessage(message) {
      const call = waiting.get(message.data.id);
      if (!call) return;
      waiting.delete(message.data.id);
      clearTimeout(call.timer);
      if (message.data.error !== undefined) call.reject(new BridgeError(message.data.error, message.data.code));
      else call.resolve(message.data.value);
    },
  });

  // The process the tab's page lives in now; its frame script is the one to ask.
  const pageProcess = browser => browser.browsingContext?.currentWindowGlobal?.osPid;

  // Ask the tab's frame script; a tab swapping processes (a cross-site navigation) gets a new one,
  // so wait until the one in the page's current process has said hello.
  const callFrame = async (tab, kind, data, timeout = 15000) => {
    if (!tab.linkedPanel) throw new BridgeError("the tab is not loaded yet; switch to it first", "not_loaded");
    const browser = tab.linkedBrowser;
    const deadline = Date.now() + timeout;
    let pid = pageProcess(browser);
    while (!browser.messageManager || !ready.get(browser)?.has(pid)) {
      if (pid === Services.appinfo.processID) {
        throw new BridgeError(`this tab shows ${browser.currentURI?.spec.slice(0, 80)}, the browser's own page; open a website in it first`, "page_refused");
      }
      if (Date.now() > deadline) throw new BridgeError("the page did not become ready in time");
      await sleep(40);
      pid = pageProcess(browser);
    }
    const id = ++nextCall;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        waiting.delete(id);
        reject(new BridgeError(`the page did not answer in ${Math.round(timeout / 1000)} s`));
      }, timeout);
      waiting.set(id, { resolve, reject, timer });
      browser.messageManager.sendAsyncMessage("Kvist:call", { id, pid, kind, data });
    });
  };

  // --- requests ------------------------------------------------------------------------------

  const methods = {
    hello: () => ({ product: `${Services.appinfo.name} ${Services.appinfo.version}`, version: VERSION }),

    tabs() {
      const front = Services.wm.getMostRecentWindow("navigator:browser");
      const list = [];
      for (const win of windows()) {
        for (const tab of win.gBrowser.tabs) {
          list.push({ id: idOf(tab), url: tab.linkedBrowser.currentURI?.spec || "", title: tab.label || "",
            active: win === front && tab === win.gBrowser.selectedTab });
        }
      }
      return list;
    },

    focused: () => idOf(frontWindow().gBrowser.selectedTab),

    new_tab() {
      const gBrowser = frontWindow().gBrowser;
      const tab = gBrowser.addTrustedTab("about:blank");
      gBrowser.selectedTab = tab;
      return idOf(tab);
    },

    close_tab({ tab }) {
      const target = tabOf(tab);
      target.ownerGlobal.gBrowser.removeTab(target);
      return null;
    },

    activate({ tab }) {
      const target = tabOf(tab);
      target.ownerGlobal.gBrowser.selectedTab = target;
      return null;
    },

    navigate({ tab, url }) {
      if (!/^https?:\/\//.test(url)) throw new BridgeError("only http(s) addresses can be opened");
      tabOf(tab).linkedBrowser.fixupAndLoadURIString(url, {
        triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal(),
      });
      return null;
    },

    evaluate: ({ tab, expression, timeout }) => callFrame(tabOf(tab), "evaluate", { expression }, timeout || 15000),

    preload({ tab, source }, client) {
      const target = tabOf(tab);
      if (source) {
        preloads.set(tab, { source, client });
        client.preloads.add(tab);
      } else {
        preloads.delete(tab);
        client.preloads.delete(tab);
      }
      const browser = target.linkedBrowser;
      const pid = target.linkedPanel && pageProcess(browser);
      if (pid && ready.get(browser)?.has(pid)) {
        browser.messageManager.sendAsyncMessage("Kvist:call", { id: 0, pid, kind: "preload", data: { source } });
      }
      return null;
    },

    async mouse(params, client) {
      await callFrame(tabOf(params.tab), "mouse", params);
      const held = `${params.tab} ${params.button}`;
      if (params.type === "mousedown") client.held.set(held, { kind: "mouse", ...params, type: "mouseup", buttons: 0 });
      if (params.type === "mouseup") client.held.delete(held);
      return null;
    },

    wheel: params => callFrame(tabOf(params.tab), "wheel", params),

    // Keys go in where a keyboard's do, at the browser window, and reach whatever has focus in the
    // page; a content process refuses keyboard input of its own (NS_ERROR_NOT_AVAILABLE).
    async key(params, client) {
      const tab = tabOf(params.tab);
      const win = tab.ownerGlobal;
      if (win.gBrowser.selectedTab !== tab) throw new BridgeError("keys go to the tab in front; switch to it first");
      const browser = tab.linkedBrowser;
      if (!browser.contains(win.document.activeElement) && win.document.activeElement !== browser) browser.focus();
      let tip = keyboards.get(win);
      if (!tip) {
        tip = Cc["@mozilla.org/text-input-processor;1"].createInstance(Ci.nsITextInputProcessor);
        keyboards.set(win, tip);
      }
      if (!tip.beginInputTransaction(win, () => true)) throw new BridgeError("another input method holds the keyboard");
      const event = new win.KeyboardEvent("", { key: params.key, code: params.code || "", keyCode: params.keyCode || 0 });
      if (params.type === "down") tip.keydown(event);
      else tip.keyup(event);
      const held = `${params.tab} key ${params.key}`;
      if (params.type === "down") client.held.set(held, { kind: "key", ...params, type: "up" });
      else client.held.delete(held);
      return null;
    },

    text: params => callFrame(tabOf(params.tab), "text", params),

    async screenshot({ tab }) {
      const target = tabOf(tab);
      const global = target.linkedBrowser.browsingContext?.currentWindowGlobal;
      if (!global) throw new BridgeError("the tab has no page to photograph");
      const bitmap = await global.drawSnapshot(null, 1, "white");
      const canvas = target.ownerDocument.createElementNS("http://www.w3.org/1999/xhtml", "canvas");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      canvas.getContext("2d").drawImage(bitmap, 0, 0);
      bitmap.close();
      const url = canvas.toDataURL("image/jpeg", 0.85);
      return url.slice(url.indexOf(",") + 1);
    },
  };

  // --- clients -------------------------------------------------------------------------------

  class Client {
    constructor(transport) {
      this.preloads = new Set();
      this.held = new Map();
      this.touched = new Set();
      this.closedAlready = false;
      this.buffer = "";
      this.decoder = new TextDecoder();
      this.input = transport.openInputStream(0, 0, 0).QueryInterface(Ci.nsIAsyncInputStream);
      this.reader = Cc["@mozilla.org/binaryinputstream;1"].createInstance(Ci.nsIBinaryInputStream);
      this.reader.setInputStream(this.input);
      this.output = transport.openOutputStream(Ci.nsITransport.OPEN_BLOCKING, 0, 0);
      this.writer = Cc["@mozilla.org/binaryoutputstream;1"].createInstance(Ci.nsIBinaryOutputStream);
      this.writer.setOutputStream(this.output);
      this.transport = transport;
      this.wait();
    }

    wait() {
      this.input.asyncWait(this, 0, 0, Services.tm.mainThread);
    }

    onInputStreamReady() {
      let count = 0;
      try {
        count = this.input.available();
      } catch (error) {
        this.close();
        return;
      }
      if (!count) {
        this.close();
        return;
      }
      this.buffer += this.decoder.decode(new Uint8Array(this.reader.readByteArray(count)), { stream: true });
      let end;
      while ((end = this.buffer.indexOf("\n")) >= 0) {
        const line = this.buffer.slice(0, end);
        this.buffer = this.buffer.slice(end + 1);
        if (line.trim()) this.handle(line);
      }
      this.wait();
    }

    async handle(line) {
      let request;
      try {
        request = JSON.parse(line);
      } catch (error) {
        return;
      }
      const method = methods[request.method];
      const params = request.params || {};
      if (params.tab) this.touched.add(params.tab);
      try {
        if (!method) throw new BridgeError(`no method ${request.method}`);
        this.send({ id: request.id, result: await method(params, this) });
      } catch (error) {
        this.send({ id: request.id, error: String(error?.message || error), code: error?.code || "" });
      }
    }

    send(message) {
      if (this.closedAlready) return;
      const bytes = new TextEncoder().encode(JSON.stringify(message) + "\n");
      try {
        this.writer.writeByteArray(bytes);
      } catch (error) {
        this.close();
      }
    }

    close() {
      if (this.closedAlready) return;
      this.closedAlready = true;
      try {
        this.transport.close(Cr.NS_OK);
      } catch (error) {
        // already closed by the other end
      }
      this.cleanUp();
    }

    // Whatever this client started must not outlive it, even when it was killed mid-step.
    async cleanUp() {
      for (const held of this.held.values()) {
        try {
          if (held.kind === "mouse") await methods.mouse(held, this);
          else await methods.key(held, this);
        } catch (error) {
          // the tab is gone; nothing is held in it any more
        }
      }
      for (const tab of this.preloads) {
        if (preloads.get(tab)?.client === this) preloads.delete(tab);
        try {
          methods.preload({ tab, source: null }, this);
        } catch (error) {
          // closed tab
        }
      }
      for (const tab of this.touched) {
        try {
          await callFrame(tabOf(tab), "evaluate", { expression: "globalThis.__kvist && globalThis.__kvist.unmount()" }, 3000);
        } catch (error) {
          // closed tab, or a page without the overlay
        }
      }
    }
  }

  // --- the socket --------------------------------------------------------------------------

  const runtime = Services.env.get("XDG_RUNTIME_DIR");
  const socketFile = new FileUtils.File(runtime && new FileUtils.File(runtime).exists() ? runtime : "/tmp");
  socketFile.append("kvist-browser-bridge.sock");
  if (socketFile.exists()) socketFile.remove(false);  // left by a browser that did not exit cleanly
  const server = Cc["@mozilla.org/network/server-socket;1"].createInstance(Ci.nsIServerSocket);
  server.initWithFilename(socketFile, 0o600, -1);
  server.asyncListen({
    onSocketAccepted(_server, transport) {
      new Client(transport);
    },
    onStopListening() {},
  });
  Services.obs.addObserver(() => {
    server.close();
    try {
      socketFile.remove(false);
    } catch (error) {
      // gone already
    }
  }, "quit-application");
})(this);
