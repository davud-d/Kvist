// One session's stream: the task's WebSocket brings the history first and then what happens now. The page keeps the
// last event number it holds, so after a lost connection it asks only for what it missed.

import { reduce } from "./feed.js";
import { refreshHours } from "./account.js";

const MAX_DELAY = 15_000;

/**
 * Opens the stream of a task into `feed`. `hooks`: onChange(), onTask(task), onAuth(), onLink("up" | "down").
 * Returns {close()}.
 */
export function openStream(taskId, feed, hooks) {
  let last = 0;
  let socket = null;
  let closed = false;
  let delay = 1000;
  let timer = 0;

  function connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/api/tasks/${taskId}/stream?after=${last}`);
    // Events before "history_end" are the past; the core's own state words must not be taken for the present.
    let history = true;
    socket.onopen = () => {
      delay = 1000;
      hooks.onLink("up");
    };
    socket.onmessage = (message) => {
      const data = JSON.parse(message.data);
      if (data.type === "event") {
        if (data.seq) {
          if (data.seq <= last) return;
          last = data.seq;
        }
        reduce(feed, data.event, { replay: history });
        hooks.onChange();
      } else if (data.type === "task") {
        feed.task = data.task;
        hooks.onTask(data.task);
        hooks.onChange();
      } else if (data.type === "history_end") {
        history = false;
        hooks.onChange(true);
      } else if (data.type === "usage") {
        refreshHours();
      } else if (data.type === "resync") {
        socket.close();
      }
    };
    socket.onclose = (event) => {
      if (closed) return;
      if (event.code === 4401) {
        hooks.onAuth();
        return;
      }
      hooks.onLink("down");
      timer = setTimeout(connect, delay);
      delay = Math.min(delay * 2, MAX_DELAY);
    };
  }
  connect();

  return {
    close() {
      closed = true;
      clearTimeout(timer);
      socket?.close();
    },
  };
}
