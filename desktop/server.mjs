// Own both local services for the lifetime of the native app. No fixed ports.
import { createServer, request as httpRequest } from "node:http";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { once } from "node:events";
import { setTimeout as delay } from "node:timers/promises";
import { root, desktopEnvironment, buildNeeded, sourceHash, recordBuild } from "./build.mjs";

process.chdir(root);
Object.assign(process.env, desktopEnvironment);
const event = (kind, value) => console.log(`MAJOR_${kind} ${JSON.stringify(value)}`);
let nextApp, webServer, api, building, socketDirectory, socketPath, baseURL;
let closing = false;

async function stopChild(child, group = false) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, "exit").catch(() => {});
  child.stdin?.end();
  const signal = name => {
    try { if (group) process.kill(-child.pid, name); else child.kill(name); } catch {}
  };
  signal("SIGTERM");
  const timer = setTimeout(() => signal("SIGKILL"), 5000);
  await exited;
  clearTimeout(timer);
}

async function stop() {
  if (closing) return;
  closing = true;
  const code = process.exitCode || 0;
  webServer?.closeAllConnections();
  webServer?.close();
  await Promise.all([stopChild(api), stopChild(building, true), nextApp?.close()]);
  if (socketDirectory) rmSync(socketDirectory, { recursive: true, force: true });
  process.exit(code);
}

function fail(error) {
  if (closing) return;
  event("ERROR", error instanceof Error ? error.message : String(error));
  console.error(error);
  process.exitCode = 1;
  void stop();
}
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
process.on("uncaughtException", fail);
process.on("unhandledRejection", fail);
process.stdin.resume();
process.stdin.on("end", stop);
process.stdout.on("error", () => void stop());

async function prepareBuild() {
  if (!buildNeeded()) return;
  event("STATUS", "Preparing the latest interface. The first opening after an update may take a minute…");
  const hash = sourceHash();
  building = spawn(process.execPath, ["node_modules/next/dist/bin/next", "build", "--webpack"], {
    cwd: root, env: desktopEnvironment, detached: true, stdio: ["ignore", "inherit", "inherit"],
  });
  const [code] = await once(building, "exit");
  building = undefined;
  if (code !== 0) throw new Error("The interface could not be prepared. Open Help → Show Startup Log for details, then try again.");
  recordBuild(hash);
}

function apiHealth() {
  return new Promise(resolve => {
    const req = httpRequest({ socketPath, path: "/api/health", timeout: 1000 }, res => {
      let body = "";
      res.on("data", chunk => { body += chunk; });
      res.on("end", () => {
        try { const data = JSON.parse(body); resolve(data.status === "ok" && data.service === "singapore-major-predictor"); }
        catch { resolve(false); }
      });
    });
    req.on("timeout", () => req.destroy());
    req.on("error", () => resolve(false));
    req.end();
  });
}

function proxyAPI(req, res) {
  const upstream = httpRequest({ socketPath, path: req.url, method: req.method, headers: { ...req.headers, host: "localhost" } }, response => {
    res.writeHead(response.statusCode, { ...response.headers, "cache-control": "no-store" });
    response.pipe(res);
  });
  // Simulations may take time; a stalled request must still fail visibly.
  upstream.setTimeout(300_000, () => upstream.destroy(new Error("The calculation took too long. Try fewer simulations.")));
  upstream.on("error", error => {
    if (res.headersSent) { res.destroy(error); return; }
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ detail: "The prediction service is unavailable. Reopen Major Forecast and try again." }));
  });
  req.on("aborted", () => upstream.destroy());
  res.on("close", () => { if (!res.writableEnded) upstream.destroy(); });
  req.pipe(upstream);
}

try {
  const python = resolve(root, ".venv/bin/python");
  if (!existsSync(python)) throw new Error("The Python environment is missing. Restore this project's .venv using the README setup steps.");
  if (!existsSync(resolve(root, "node_modules/next"))) throw new Error("The web dependencies are missing. Install this project's dependencies using the README setup steps.");
  await prepareBuild();
  if (!closing) {
    event("STATUS", "Opening your saved tournament…");
    // Short path avoids macOS's Unix socket path limit; mkdtemp creates mode 0700.
    socketDirectory = mkdtempSync("/tmp/major-forecast-");
    socketPath = resolve(socketDirectory, "api.sock");
    api = spawn(python, [resolve(root, "desktop/api.py"), socketPath], {
      cwd: root, env: { ...process.env, PYTHONUNBUFFERED: "1" }, stdio: ["pipe", "inherit", "inherit"],
    });
    api.on("error", fail);
    api.on("exit", code => { if (!closing) fail(new Error(`The prediction service stopped (${code}). See Help → Show Startup Log, then try again.`)); });
    const deadline = Date.now() + 60_000;
    while (!await apiHealth()) {
      if (closing) break;
      if (Date.now() > deadline) throw new Error("The prediction service did not respond. Open Help → Show Startup Log for details.");
      await delay(250);
    }
    if (!closing) {
      const { default: next } = await import("next");
      nextApp = next({ dev: false, dir: root, hostname: "127.0.0.1" });
      await nextApp.prepare();
      const handle = nextApp.getRequestHandler();
      webServer = createServer((req, res) => {
        // Reject cross-site access and DNS rebinding to this local data workspace.
        if (req.headers.host !== new URL(baseURL).host ||
            (req.headers.origin && req.headers.origin !== baseURL) ||
            req.headers["sec-fetch-site"] === "cross-site") {
          res.writeHead(403, { "content-type": "application/json" });
          res.end(JSON.stringify({ detail: "Open this request inside Major Forecast." }));
          return;
        }
        if (req.url.startsWith("/api/")) proxyAPI(req, res);
        else Promise.resolve(handle(req, res)).catch(error => { console.error(error); if (!res.headersSent) res.writeHead(500); res.end(); });
      });
      webServer.listen(0, "127.0.0.1");
      await once(webServer, "listening");
      baseURL = `http://127.0.0.1:${webServer.address().port}`;
      event("READY", { url: baseURL });
    }
  }
} catch (error) { fail(error); }
