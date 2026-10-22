import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const packaged = existsSync(resolve(root, "bundled.json"));
export const desktopEnvironment = {
  ...process.env,
  NODE_ENV: "production",
  MAJOR_DESKTOP: "1",
  NEXT_PUBLIC_API_URL: "", // Same-origin API proxy, including after a port change.
  NEXT_TELEMETRY_DISABLED: "1",
};
const manifest = resolve(root, ".next-desktop/desktop-source.sha256");

export function sourceHash() {
  const hash = createHash("sha256");
  function visit(relative) {
    const path = resolve(root, relative);
    hash.update(relative);
    if (!existsSync(path)) { hash.update("missing"); return; }
    if (statSync(path).isDirectory()) {
      for (const name of readdirSync(path).sort()) visit(`${relative}/${name}`);
    } else hash.update(readFileSync(path));
  }
  for (const path of ["src", "public", "next.config.ts", "package-lock.json", "package.json", "tsconfig.json", "tailwind.config.ts", "postcss.config.mjs", "desktop/build.mjs"]) visit(path);
  return hash.digest("hex");
}

export function buildNeeded() {
  if (packaged) return false;
  return !existsSync(resolve(root, ".next-desktop/BUILD_ID")) || !existsSync(manifest) || readFileSync(manifest, "utf8") !== sourceHash();
}

export function recordBuild(hash) { writeFileSync(manifest, hash); }
