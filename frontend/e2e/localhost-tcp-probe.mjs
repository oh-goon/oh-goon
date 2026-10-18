import { performance } from "node:perf_hooks";

export const LOCALHOST_IPV4 = "127.0.0.1";

export function socketEvidence(socket) {
  return {
    localAddress: socket.localAddress ?? null,
    localPort: socket.localPort ?? null,
    remoteAddress: socket.remoteAddress ?? null,
    remotePort: socket.remotePort ?? null,
  };
}

export function waitForSocketConnect({ socket, deadlineMs, host, port, destroyOnSuccess = true }) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const started = performance.now();
    const finish = (fn, value, { destroy = true } = {}) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.removeListener("connect", onConnect);
      socket.removeListener("error", onError);
      if (destroy) socket.destroy();
      fn(value);
    };
    const onConnect = () => finish(
      resolve,
      { elapsedMs: performance.now() - started, socket: socketEvidence(socket) },
      { destroy: destroyOnSuccess },
    );
    const onError = (error) => finish(reject, error);
    const timer = setTimeout(() => {
      const error = new Error(`connect localhost TCP probe did not settle within ${deadlineMs} ms`);
      error.code = "OH_GOON_LOCALHOST_TRANSPORT_TIMEOUT";
      error.host = host;
      error.port = port;
      error.deadlineMs = deadlineMs;
      finish(reject, error);
    }, deadlineMs);
    socket.once("connect", onConnect);
    socket.once("error", onError);
  });
}
