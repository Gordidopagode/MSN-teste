import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const builtDir = join(projectDir, "hub_source", "client", "public");
const launcherDir = join(projectDir, "client", "public");
const builtIndex = join(builtDir, "index.html");

if (!existsSync(builtIndex)) {
  throw new Error(`Bundle do Hub não encontrado em ${builtIndex}`);
}

mkdirSync(join(launcherDir, "assets"), { recursive: true });

for (const filename of readdirSync(join(launcherDir, "assets"))) {
  if (/^index-[^/]+\.(js|css)$/.test(filename)) {
    rmSync(join(launcherDir, "assets", filename), { force: true });
  }
}

const html = readFileSync(builtIndex, "utf8");
const assetRefs = [...html.matchAll(/assets\/(index-[^" ]+\.(?:js|css))/g)].map((match) => match[1]);
if (assetRefs.length === 0) {
  throw new Error("O index.html compilado não referencia os assets do Hub.");
}

cpSync(builtIndex, join(launcherDir, "index.html"));
for (const asset of assetRefs) {
  cpSync(join(builtDir, "assets", asset), join(launcherDir, "assets", asset));
}

console.log(`Bundle sincronizado para ${launcherDir}: ${assetRefs.join(", ")}`);
