import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { root, desktopEnvironment, buildNeeded, sourceHash, recordBuild } from "../desktop/build.mjs";

if (process.platform !== "darwin") throw new Error("The desktop app requires macOS.");
process.chdir(root);
const run = (command, args, options = {}) => execFileSync(command, args, { cwd: root, stdio: "inherit", ...options });
if (!existsSync(resolve(root, ".venv/bin/python"))) throw new Error("Set up the project's Python environment first. See README Quick start.");
if (buildNeeded()) {
  const hash = sourceHash();
  run(process.execPath, ["node_modules/next/dist/bin/next", "build", "--webpack"], { env: desktopEnvironment });
  recordBuild(hash);
}
const output = resolve(root, "desktop/dist");
const app = resolve(output, "Major Forecast.app");
rmSync(app, { recursive: true, force: true });
mkdirSync(resolve(app, "Contents/MacOS"), { recursive: true });
mkdirSync(resolve(app, "Contents/Resources"), { recursive: true });
const flags = ["-fobjc-arc", "-O2", "-mmacosx-version-min=13.0"];
run("/usr/bin/clang", [...flags, "-framework", "Cocoa", "-framework", "WebKit", "-framework", "UniformTypeIdentifiers", "desktop/App.m", "-o", resolve(app, "Contents/MacOS/MajorForecast")]);
run("/usr/bin/clang", [...flags, "-framework", "Cocoa", "desktop/make-icon.m", "-o", resolve(output, "make-icon")]);
run(resolve(output, "make-icon"), [resolve(output, "AppIcon.iconset")]);
run("/usr/bin/iconutil", ["-c", "icns", resolve(output, "AppIcon.iconset"), "-o", resolve(app, "Contents/Resources/AppIcon.icns")]);
copyFileSync(resolve(root, "scripts/macos/Info.plist"), resolve(app, "Contents/Info.plist"));
writeFileSync(resolve(app, "Contents/Resources/launcher.json"), JSON.stringify({ projectPath: root, nodePath: process.execPath }, null, 2));
run("/usr/bin/codesign", ["--force", "--sign", "-", app]);
run("/usr/bin/codesign", ["--verify", "--deep", "--strict", app]);
console.log(`Desktop app built: ${app}`);
