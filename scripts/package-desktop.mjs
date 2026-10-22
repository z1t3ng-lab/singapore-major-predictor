// macOS builder: packages a complete, relocatable app and a drag-to-install disk image.
import { execFileSync } from 'node:child_process';
import { cpSync, copyFileSync, existsSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { root } from '../desktop/build.mjs';
if (process.platform !== 'darwin') throw new Error('Build this package on macOS.');
const run = (cmd, args, options={}) => execFileSync(cmd,args,{cwd:root,stdio:'inherit',...options});
run(process.execPath,['scripts/build-desktop.mjs']);
const app=resolve(root,'desktop/dist/Major Forecast.app');
const resources=join(app,'Contents/Resources');
const workspace=join(resources,'workspace');
mkdirSync(workspace,{recursive:true});
// Explicit allowlist excludes local databases, environment files, exports, logs and backups.
for (const name of ['node_modules','public','templates','package.json','.next-desktop']) {
  cpSync(resolve(root,name),join(workspace,name),{recursive:true,verbatimSymlinks:true,filter:p=>!p.includes('/.next-desktop/cache')});
}
mkdirSync(join(workspace,'desktop'));
for (const name of ['api.py','server.mjs','build.mjs']) copyFileSync(resolve(root,'desktop',name),join(workspace,'desktop',name));
mkdirSync(join(workspace,'backend/data'),{recursive:true});
cpSync(resolve(root,'backend/app'),join(workspace,'backend/app'),{recursive:true,verbatimSymlinks:true,filter:p=>!p.includes('__pycache__')});
for(const name of ['schema.sql','demo_seed.json','migrations']) cpSync(resolve(root,'backend/data',name),join(workspace,'backend/data',name),{recursive:true});
writeFileSync(join(workspace,'next.config.mjs'),'export default {distDir:".next-desktop",reactStrictMode:true,poweredByHeader:false};\n');
writeFileSync(join(workspace,'bundled.json'),JSON.stringify({version:'1.2.0',architecture:process.arch}));
mkdirSync(join(workspace,'runtime'));
copyFileSync(process.execPath,join(workspace,'runtime/node'));
const nodeLibraries=execFileSync('/usr/bin/otool',['-L',process.execPath],{encoding:'utf8'});
if(nodeLibraries.split('\n').slice(1).some(line=>line.startsWith('\t')&&line.trim().startsWith('/')&&!line.trim().startsWith('/usr/lib/')&&!line.trim().startsWith('/System/'))) throw new Error('Node has external libraries. Use the official self-contained Node distribution.');
run(resolve(root,'.venv/bin/python'),['scripts/bundle-runtime.py',join(workspace,'runtime/python')]);
const nodeLicense='/usr/local/share/doc/node/LICENSE';
if (existsSync(nodeLicense)) copyFileSync(nodeLicense,join(workspace,'runtime/NODE-LICENSE'));
else {
  const license = await fetch(`https://raw.githubusercontent.com/nodejs/node/${process.version}/LICENSE`, {signal:AbortSignal.timeout(30000)});
  if(!license.ok) throw new Error('Could not retrieve the Node redistribution license.');
  writeFileSync(join(workspace,'runtime/NODE-LICENSE'),await license.text());
}
writeFileSync(join(resources,'launcher.json'),JSON.stringify({bundled:true,version:'1.2.0'}));
run('/usr/bin/codesign',['--force','--deep','--sign','-',app]);
run('/usr/bin/codesign',['--verify','--deep','--strict',app]);
run(resolve(root,'.venv/bin/python'),['scripts/smoke-desktop.py']);
const staging=resolve(root,'desktop/dist/dmg');
rmSync(staging,{recursive:true,force:true});mkdirSync(staging);
run('/usr/bin/ditto',[app,join(staging,'Major Forecast.app')]);
symlinkSync('/Applications',join(staging,'Applications'));
writeFileSync(join(staging,'Read me.txt'),'Major Forecast 1.2.0\n\nDrag Major Forecast to Applications, then open it. No Terminal, Node.js or Python installation is required.\nData is saved in ~/Library/Application Support/Major Forecast.\nThis build is locally signed, not Apple notarized. For distribution, a developer must sign and notarize with their Apple Developer credentials.\n');
const dmg=resolve(root,`desktop/dist/Major-Forecast-1.2.0-macOS-${process.arch}.dmg`);
run('/usr/bin/hdiutil',['create','-volname','Major Forecast','-srcfolder',staging,'-ov','-format','UDZO',dmg]);
rmSync(staging,{recursive:true,force:true});
console.log(`Standalone app: ${app}\nInstaller: ${dmg}`);
