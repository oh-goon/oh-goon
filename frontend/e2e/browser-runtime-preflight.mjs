import net from "node:net";

const reuseExistingServer = process.env.OH_GOON_BROWSER_REUSE_SERVER === "1";
const host = "127.0.0.1";
const ports = [5173, 8787];

function isListening(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    socket.setTimeout(500);
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    const unavailable = () => {
      socket.destroy();
      resolve(false);
    };
    socket.once("error", unavailable);
    socket.once("timeout", unavailable);
  });
}

if (reuseExistingServer) {
  console.log("Browser runtime preflight skipped because OH_GOON_BROWSER_REUSE_SERVER=1 explicitly delegates fixture ownership to an existing server.");
} else {
  const occupied = [];
  for (const port of ports) {
    if (await isListening(port)) occupied.push(port);
  }
  if (occupied.length > 0) {
    console.error(`Browser certification requires a clean runtime. Loopback port(s) already in use: ${occupied.join(", ")}.`);
    console.error("Stop the previous `make dev`/Vite/Uvicorn runtime before Gate D; certification must not reuse an unowned server.");
    process.exitCode = 2;
  } else {
    console.log("Browser runtime preflight passed: 127.0.0.1:5173 and :8787 are free for Playwright-owned fixture startup.");
  }
}
