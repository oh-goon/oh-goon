import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import net from "node:net";
import test from "node:test";
import { verifyLocalhostTransport } from "./browser-localhost-transport.mjs";

const NONCE = Buffer.from("oh-goon-localhost-proof");

class HangingSocket extends EventEmitter {
  write() {}
  destroy() { this.destroyed = true; this.emit("close"); }
}

test("real Node localhost transport binds, accepts, exchanges payload, and releases ownership", async () => {
  const result = await verifyLocalhostTransport({ deadlineMs: 1000, createNonce: () => NONCE });
  assert.equal(result.host, "127.0.0.1");
  assert.ok(result.liveElapsedMs < 1000);
  assert.equal(result.payloadBytes, NONCE.length);
  assert.equal(result.acceptedConnections, 1);
});

test("transport gate bounds a hanging Node connection instead of inheriting the kernel timeout", async () => {
  const hanging = new HangingSocket();
  await assert.rejects(
    verifyLocalhostTransport({ deadlineMs: 25, createConnection: () => hanging, createNonce: () => NONCE }),
    (error) => error?.code === "OH_GOON_LOCALHOST_TRANSPORT_TIMEOUT",
  );
  assert.equal(hanging.destroyed, true);
});

test("transport gate rejects a connection that never reaches the repository-owned listener", async () => {
  class ForeignSocket extends EventEmitter {
    constructor() { super(); queueMicrotask(() => this.emit("connect")); }
    write() {}
    destroy() { this.destroyed = true; this.emit("close"); }
  }
  await assert.rejects(
    verifyLocalhostTransport({ deadlineMs: 25, createConnection: () => new ForeignSocket(), createNonce: () => NONCE }),
    (error) => error?.code === "OH_GOON_LOCALHOST_TRANSPORT_PAYLOAD_TIMEOUT",
  );
});

test("transport gate rejects payload corruption instead of treating connect as sufficient", async () => {
  const serverFactory = (handler) => net.createServer((socket) => {
    socket.on("data", () => socket.write(Buffer.from("x".repeat(NONCE.length))));
    handler = null;
  });
  await assert.rejects(
    verifyLocalhostTransport({ deadlineMs: 1000, createServer: serverFactory, createNonce: () => NONCE }),
    (error) => error?.code === "OH_GOON_LOCALHOST_TRANSPORT_PAYLOAD_MISMATCH",
  );
});
