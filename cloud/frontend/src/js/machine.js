// The person's machine, polled while it is being prepared. Creating and waking happen on the
// server in the background; screens only show where that is.
import { api } from "./api.js";

const SETTLED = new Set(["ready", "working", "waiting_for_you", "error", "asleep"]);

export function watchMachine(onChange, { interval = 2000 } = {}) {
  let stopped = false;
  let timer = null;
  const tick = async () => {
    let machine = null;
    try {
      const machines = await api("/api/machines");
      machine = machines[0] || null;
    } catch {
      machine = null;
    }
    if (stopped) return;
    onChange(machine);
    if (!machine || !SETTLED.has(machine.display_status)) timer = setTimeout(tick, interval);
  };
  tick();
  return () => { stopped = true; clearTimeout(timer); };
}
