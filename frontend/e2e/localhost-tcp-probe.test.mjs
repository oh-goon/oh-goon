import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { waitForSocketConnect } from "./localhost-tcp-probe.mjs";

class FakeSocket extends EventEmitter {
  destroy() { this.destroyed = true; }
}

test("connect probe resolves with socket evidence and destroys by default", async () => {
  const socket = new FakeSocket();
  socket.localAddress = "127.0.0.1"; socket.localPort = 1111;
  socket.remoteAddress = "127.0.0.1"; socket.remotePort = 2222;
  const result = waitForSocketConnect({ socket, deadlineMs: 100, host: "127.0.0.1", port: 2222 });
  socket.emit("connect");
  assert.deepEqual((await result).socket, {
    localAddress: "127.0.0.1", localPort: 1111, remoteAddress: "127.0.0.1", remotePort: 2222,
  });
  assert.equal(socket.destroyed, true);
});

test("connect probe can transfer live socket ownership to the caller", async () => {
  const socket = new FakeSocket();
  const result = waitForSocketConnect({ socket, deadlineMs: 100, host: "127.0.0.1", port: 2222, destroyOnSuccess: false });
  socket.emit("connect");
  await result;
  assert.notEqual(socket.destroyed, true);
});

test("connect probe propagates connection error and destroys socket", async () => {
  const socket = new FakeSocket();
  const expected = Object.assign(new Error("refused"), { code: "ECONNREFUSED" });
  const result = waitForSocketConnect({ socket, deadlineMs: 100, host: "127.0.0.1", port: 2222 });
  socket.emit("error", expected);
  await assert.rejects(result, (error) => error === expected);
  assert.equal(socket.destroyed, true);
});

test("connect probe timeout is bounded, typed, and destroys the socket", async () => {
  const socket = new FakeSocket();
  await assert.rejects(
    waitForSocketConnect({ socket, deadlineMs: 20, host: "127.0.0.1", port: 2222 }),
    (error) => error?.code === "OH_GOON_LOCALHOST_TRANSPORT_TIMEOUT" && error.deadlineMs === 20,
  );
  assert.equal(socket.destroyed, true);
});

test("connect probe settles exactly once when a late event follows timeout", async () => {
  const socket = new FakeSocket();
  const result = waitForSocketConnect({ socket, deadlineMs: 5, host: "127.0.0.1", port: 2222 });
  await assert.rejects(result, (error) => error?.code === "OH_GOON_LOCALHOST_TRANSPORT_TIMEOUT");
  assert.doesNotThrow(() => socket.emit("connect"));
  assert.equal(socket.listenerCount("connect"), 0);
  assert.equal(socket.listenerCount("error"), 0);
});
