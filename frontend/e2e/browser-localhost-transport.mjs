import crypto from "node:crypto";
import net from "node:net";
import { LOCALHOST_IPV4, waitForSocketConnect } from "./localhost-tcp-probe.mjs";

export const DEFAULT_LOCALHOST_TRANSPORT_DEADLINE_MS = 1000;
const HOST = LOCALHOST_IPV4;

function transportError(code, message, port, extra = {}) {
  const error = new Error(message);
  error.code = code;
  error.host = HOST;
  error.port = port;
  Object.assign(error, extra);
  return error;
}

function listenEphemeral(createServer, onConnection) {
  return new Promise((resolve, reject) => {
    const server = createServer(onConnection);
    server.once("error", reject);
    server.listen(0, HOST, () => resolve(server));
  });
}

async function destroyOwnedSockets(sockets) {
  const pending = [...sockets].map((socket) => new Promise((resolve) => {
    if (socket.destroyed) { resolve(); return; }
    socket.once("close", resolve);
    socket.destroy();
  }));
  await Promise.all(pending);
}

function closeServer(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}

function exchangeNonce({ socket, nonce, deadlineMs, port }) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let received = Buffer.alloc(0);
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.removeAllListeners();
      socket.destroy();
      fn(value);
    };
    const timer = setTimeout(() => finish(
      reject,
      transportError(
        "OH_GOON_LOCALHOST_TRANSPORT_PAYLOAD_TIMEOUT",
        `localhost TCP payload proof did not settle within ${deadlineMs} ms`,
        port,
        { deadlineMs },
      ),
    ), deadlineMs);
    socket.on("data", (chunk) => {
      received = Buffer.concat([received, chunk]);
      if (received.length < nonce.length) return;
      if (!received.subarray(0, nonce.length).equals(nonce)) {
        finish(reject, transportError(
          "OH_GOON_LOCALHOST_TRANSPORT_PAYLOAD_MISMATCH",
          "localhost TCP payload proof returned the wrong nonce",
          port,
        ));
        return;
      }
      finish(resolve, { bytes: nonce.length });
    });
    socket.once("error", (error) => finish(reject, error));
    socket.write(nonce);
  });
}

/**
 * Prove the transport property the browser harness actually requires: Node can bind an
 * IPv4 loopback listener, connect to that exact listener, and exchange bytes both ways.
 *
 * A connection to a recently closed port is deliberately not used as health evidence.
 * WSL/host forwarding layers may transiently retain or reuse that endpoint; that says
 * nothing about whether Vite/Playwright can use a live repository-owned listener.
 */
export async function verifyLocalhostTransport({
  deadlineMs = DEFAULT_LOCALHOST_TRANSPORT_DEADLINE_MS,
  createServer = (handler) => net.createServer(handler),
  createConnection = (options) => net.createConnection(options),
  createNonce = () => crypto.randomBytes(32),
} = {}) {
  const accepted = new Set();
  const nonce = createNonce();
  let acceptedCount = 0;
  let server;
  const onConnection = (socket) => {
    acceptedCount += 1;
    accepted.add(socket);
    socket.once("close", () => accepted.delete(socket));
    socket.pipe(socket);
  };

  server = await listenEphemeral(createServer, onConnection);
  const address = server.address();
  if (!address || typeof address === "string") {
    await closeServer(server);
    throw new Error("localhost transport preflight could not obtain its ephemeral IPv4 port");
  }
  const port = address.port;

  try {
    const client = createConnection({ host: HOST, port });
    const live = await waitForSocketConnect({
      socket: client, deadlineMs, host: HOST, port, destroyOnSuccess: false,
    });
    const payload = await exchangeNonce({ socket: client, nonce, deadlineMs, port });
    if (acceptedCount !== 1) {
      throw transportError(
        "OH_GOON_LOCALHOST_TRANSPORT_ACCEPTANCE_UNPROVEN",
        `localhost listener accepted ${acceptedCount} connections, expected exactly 1`,
        port,
        { acceptedCount },
      );
    }
    await destroyOwnedSockets(accepted);
    await closeServer(server);
    if (server.listening || server.address() !== null || accepted.size !== 0) {
      throw transportError(
        "OH_GOON_LOCALHOST_TRANSPORT_SERVER_CLOSE_UNPROVEN",
        "localhost probe server did not release its owned listener and connections",
        port,
        { acceptedConnections: accepted.size },
      );
    }
    return {
      host: HOST,
      port,
      liveElapsedMs: live.elapsedMs,
      payloadBytes: payload.bytes,
      acceptedConnections: acceptedCount,
    };
  } catch (error) {
    await destroyOwnedSockets(accepted);
    if (server?.listening) await closeServer(server);
    throw error;
  }
}
