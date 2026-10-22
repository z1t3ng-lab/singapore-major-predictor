import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, lstatSync, readlinkSync, mkdirSync, renameSync, rmSync, symlinkSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { root } from "../desktop/build.mjs";

if (process.platform !== "darwin") throw new Error("The desktop app requires macOS.");
const id = "com.zitengzhou.majorforecast";
const source = resolve(root, "desktop/dist/Major Forecast.app");
const target = join(homedir(), "Applications/Major Forecast.app");
const shortcut = join(homedir(), "Desktop/Major Forecast.app");
const present = path => { try { return lstatSync(path); } catch (error) { if (error.code === "ENOENT") return null; throw error; } };
const appID = path => execFileSync("/usr/libexec/PlistBuddy", ["-c", "Print :CFBundleIdentifier", join(path, "Contents/Info.plist")], { encoding: "utf8" }).trim();
if (!existsSync(source)) throw new Error("Build the desktop app first: npm run desktop:build.");
if (appID(source) !== id) throw new Error("The built app has an unexpected identity.");
const desktopItem = present(shortcut);
if (desktopItem && !(desktopItem.isSymbolicLink() && readlinkSync(shortcut) === target)) {
  if (!desktopItem.isDirectory() || appID(shortcut) !== id) throw new Error("An unrelated Desktop item already has this name. Move that item before installing.");
}
if (present(target) && (lstatSync(target).isSymbolicLink() || appID(target) !== id)) throw new Error("An unrelated application already has this name. It was not changed.");
mkdirSync(join(homedir(), "Applications"), { recursive: true });
const staged = `${target}.installing-${Date.now()}`;
const backup = `${target}.previous-${Date.now()}`;
execFileSync("/usr/bin/ditto", [source, staged]);
try { execFileSync("/usr/bin/codesign", ["--verify", "--deep", "--strict", staged]); }
catch (error) { rmSync(staged, {recursive:true, force:true}); throw error; }
// On the first bundled install, copy the existing database with SQLite's backup API.
// Later upgrades retain the independent Application Support database.
const config = JSON.parse(readFileSync(join(source, "Contents/Resources/launcher.json"), "utf8"));
const oldDatabase = join(root, "backend/data/major.sqlite3");
const dataFolder = join(homedir(), "Library/Application Support/Major Forecast");
const newDatabase = join(dataFolder, "major.sqlite3");
if (config.bundled && existsSync(oldDatabase) && !existsSync(newDatabase)) {
  mkdirSync(dataFolder, { recursive: true });
  const python = join(source, "Contents/Resources/workspace/runtime/python/bin/python3.12");
  execFileSync(python, ["-c", "import sqlite3,sys; a=sqlite3.connect(sys.argv[1]); b=sqlite3.connect(sys.argv[2]); a.backup(b); b.close(); a.close()", oldDatabase, newDatabase], {env:{...process.env,PYTHONHOME:join(source,"Contents/Resources/workspace/runtime/python"),PYTHONNOUSERSITE:"1",PYTHONDONTWRITEBYTECODE:"1"}});
  console.log(`Existing tournament copied to: ${newDatabase}`);
}
const hadApp = existsSync(target);
if (hadApp) renameSync(target, backup);
try { renameSync(staged, target); }
catch (error) { if (hadApp) renameSync(backup, target); rmSync(staged, { recursive: true, force: true }); throw error; }
// Replace only the old Major Forecast browser launcher, retaining a rollback copy.
if (desktopItem?.isDirectory()) {
  const old = join(homedir(), `Applications/Major Forecast Browser Launcher.previous-${Date.now()}.app`);
  renameSync(shortcut, old);
  console.log(`Previous browser launcher retained: ${old}`);
}
if (!present(shortcut)) symlinkSync(target, shortcut);
console.log(`Installed: ${target}\nDesktop icon: ${shortcut}`);
