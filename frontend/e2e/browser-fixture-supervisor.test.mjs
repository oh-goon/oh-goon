import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import {
  BrowserFixtureStartupError,
  boundedStartupTail,
  redactDiagnosticLine,
  waitForFixtureReady,
} from "./browser-fixture-supervisor.mjs";

class FakeChild extends EventEmitter {}
const immediate = async () => {};

async function rejectedCode(action, expected) {
  await assert.rejects(action, (error) => {
    assert.ok(error instanceof BrowserFixtureStartupError);
    assert.equal(error.code, expected);
    return true;
  });
}

test("fixture supervisor reports process exit before readiness", async () => {
  const child = new FakeChild();
  queueMicrotask(() => child.emit("exit", 23, null));
  await rejectedCode(
    () => waitForFixtureReady({ child, deadlineMs: 20, intervalMs: 1, tcpProbe: async () => ({ ok: false }), sleep: immediate }),
    "BROWSER_FIXTURE_PROCESS_EXITED",
  );
});

test("fixture supervisor reports spawn failure separately from readiness timeout", async () => {
  const child = new FakeChild();
  queueMicrotask(() => {
    const error = new Error("spawn npm ENOENT");
    error.code = "ENOENT";
    child.emit("error", error);
  });
  await rejectedCode(
    () => waitForFixtureReady({ child, deadlineMs: 20, intervalMs: 1, tcpProbe: async () => ({ ok: false }), sleep: immediate }),
    "BROWSER_FIXTURE_PROCESS_SPAWN_FAILED",
  );
});

test("fixture supervisor distinguishes port-not-bound from HTTP-not-ready", async () => {
  await rejectedCode(
    () => waitForFixtureReady({ child: new FakeChild(), deadlineMs: 2, intervalMs: 0, tcpProbe: async () => ({ ok: false }), sleep: immediate }),
    "BROWSER_FIXTURE_PORT_NOT_BOUND",
  );
  await rejectedCode(
    () => waitForFixtureReady({ child: new FakeChild(), deadlineMs: 2, intervalMs: 0, tcpProbe: async () => ({ ok: true }), httpProbe: async () => ({ ok: false, statusCode: 503 }), sleep: immediate }),
    "BROWSER_FIXTURE_HTTP_NOT_READY",
  );
});

test("fixture supervisor returns bounded readiness milestones", async () => {
  let tcpCalls = 0;
  const result = await waitForFixtureReady({
    child: new FakeChild(),
    deadlineMs: 50,
    intervalMs: 0,
    tcpProbe: async () => ({ ok: ++tcpCalls >= 2 }),
    httpProbe: async () => ({ ok: true, statusCode: 200 }),
    sleep: immediate,
  });
  assert.equal(result.httpStatus, 200);
  assert.ok(result.firstBoundMs !== null);
});

test("startup tail is bounded and redacts common secret forms", () => {
  assert.equal(redactDiagnosticLine("Authorization: Bearer abc123"), "Authorization: Bearer <redacted>");
  assert.equal(redactDiagnosticLine("token=abc123"), "token=<redacted>");
  assert.equal(redactDiagnosticLine("http://user:pass@example.test"), "http://user:<redacted>@example.test");
  assert.deepEqual(boundedStartupTail(["one", "two", "three"], 2), ["two", "three"]);
});
