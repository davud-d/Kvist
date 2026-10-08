"use strict"

const { contextBridge, ipcRenderer } = require("electron")

contextBridge.exposeInMainWorld("kvistTray", {
  onView: (listener) => ipcRenderer.on("tray:view", (_event, view) => listener(view)),
  onOpen: (listener) => ipcRenderer.on("tray:open", (_event, origin) => listener(origin)),
  hide: () => ipcRenderer.send("tray:hide"),
  ready: () => ipcRenderer.send("tray:ready"),
  select: (id) => ipcRenderer.send("tray:select", id),
  focusTerminal: (id) => ipcRenderer.send("tray:focus", id),
  disable: () => ipcRenderer.send("tray:disable"),
})
