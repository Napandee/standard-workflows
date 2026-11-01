// Release screenshots of the static demo, dark theme, written as JPEGs:
//   <name>-desktop.jpg  the first recorded page (/) at 1440x900
//   <name>-mobile.jpg    the same page at 393x852 (phone)
//   <name>-hero.jpg       both, in a CSS browser frame and a CSS phone frame
//
//   node capture.mjs <siteDir> <outDir> [--config demo/demo.config.json]   (local: run.sh capture --project <dir>)
//
// A failed desktop or mobile shot is logged and skipped; the exit code is non-zero
// only if the hero cannot be made (it needs both shots). The browser clock and time
// zone are pinned to demo.config.json's "now", so the pictures do not change from
// run to run. Dark comes from colorScheme: the project's app must follow
// prefers-color-scheme for the shots to be dark.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "playwright";
import { startServer, siteBase } from "./serve.mjs";

const QUALITY = 84;
/** now / name / siteLabel from the project's demo.config.json. */
export function readConfig(file) {
  const c = JSON.parse(fs.readFileSync(file, "utf8"));
  for (const k of ["now", "name", "siteLabel"]) if (!c[k]) throw new Error(`${file}: "${k}" is required`);
  return { now: c.now, name: c.name, siteLabel: c.siteLabel };
}
let CONFIG = { now: "2026-01-01T12:00:00Z", name: "demo", siteLabel: "" };
/** Sets the config the shots use (main() does this from --config). */
export function configure(c) { CONFIG = { ...CONFIG, ...c }; }

const SAME = { timezoneId: "UTC", locale: "en-US", colorScheme: "dark" };
export const SHOTS = {
  desktop: { ...SAME, viewport: { width: 1440, height: 900 } },
  mobile: { ...SAME, viewport: { width: 393, height: 852 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
};

/** Opens <url> with the pinned clock and returns a PNG buffer of the viewport. */
export async function shoot(browser, url, options) {
  const context = await browser.newContext(options);
  try {
    await context.clock.setFixedTime(new Date(CONFIG.now));
    const page = await context.newPage();
    const resp = await page.goto(url, { waitUntil: "load" });
    if (!resp || resp.status() !== 200) throw new Error(`${url} answered ${resp ? resp.status() : "nothing"}`);
    await page.waitForLoadState("networkidle");
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(300);
    return await page.screenshot({ type: "png" });
  } finally {
    await context.close();
  }
}

export function heroHtml(desktopPng, mobilePng) {
  const uri = (b) => `data:image/png;base64,${b.toString("base64")}`;
  return `<!doctype html><meta charset="utf-8"><style>
*{box-sizing:border-box;margin:0}
body{width:1800px;height:1000px;overflow:hidden;position:relative;font-family:system-ui,sans-serif;
  background:radial-gradient(120% 120% at 20% 10%,#2b3358 0%,#14172b 55%,#0b0d1a 100%)}
.browser{position:absolute;left:70px;top:130px;width:1180px;border-radius:14px;background:#1c2038;
  box-shadow:0 40px 90px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.08);overflow:hidden}
.bar{height:44px;display:flex;align-items:center;gap:8px;padding:0 16px;background:#252a47}
.dot{width:12px;height:12px;border-radius:50%}
.url{margin-left:18px;flex:0 1 420px;height:26px;border-radius:13px;background:#171a30;color:#8f96b8;
  font-size:13px;line-height:26px;padding:0 14px}
.browser img{display:block;width:1180px;height:auto}
.phone{position:absolute;left:1290px;top:60px;width:424px;padding:14px;border-radius:56px;background:#0a0b14;
  box-shadow:0 40px 90px rgba(0,0,0,.65),0 0 0 2px #3a4064,inset 0 0 0 2px #1d2036}
.phone img{display:block;width:396px;height:auto;border-radius:42px}
</style>
<div class="browser"><div class="bar"><span class="dot" style="background:#ff5f57"></span>
<span class="dot" style="background:#febc2e"></span><span class="dot" style="background:#28c840"></span>
<span class="url">${CONFIG.siteLabel}</span></div><img src="${uri(desktopPng)}"></div>
<div class="phone"><img src="${uri(mobilePng)}"></div>`;
}

async function jpeg(browser, png, viewport, file) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  try {
    const page = await context.newPage();
    await page.setContent(`<style>*{margin:0}</style><img src="data:image/png;base64,${png.toString("base64")}" style="width:${viewport.width}px;display:block">`);
    fs.writeFileSync(file, await page.screenshot({ type: "jpeg", quality: QUALITY }));
  } finally {
    await context.close();
  }
}

/**
 * Takes the three pictures. <steps> may replace the page shots (for tests).
 * Resolves to { failed: [names], heroOk }.
 */
export async function capture(browser, url, outDir, steps = {}) {
  fs.mkdirSync(outDir, { recursive: true });
  const take = steps.shoot || shoot;
  const shots = {};
  const failed = [];
  for (const name of ["desktop", "mobile"]) {
    try {
      shots[name] = await take(browser, url, SHOTS[name]);
      const k = SHOTS[name].deviceScaleFactor || 1;
      const { width, height } = SHOTS[name].viewport;
      await jpeg(browser, shots[name], { width: width * k, height: height * k }, path.join(outDir, `${CONFIG.name}-${name}.jpg`));
      console.log(`ok   ${CONFIG.name}-${name}.jpg`);
    } catch (e) {
      failed.push(name);
      console.log(`FAIL ${CONFIG.name}-${name}.jpg: ${e.message.split("\n")[0]}`);
    }
  }
  let heroOk = false;
  try {
    if (failed.length) throw new Error(`needs the ${failed.join(" and ")} shot`);
    const context = await browser.newContext({ viewport: { width: 1800, height: 1000 } });
    try {
      const page = await context.newPage();
      await (steps.compose || ((p) => p.setContent(heroHtml(shots.desktop, shots.mobile))))(page);
      fs.writeFileSync(path.join(outDir, `${CONFIG.name}-hero.jpg`), await page.screenshot({ type: "jpeg", quality: QUALITY }));
    } finally {
      await context.close();
    }
    heroOk = true;
    console.log(`ok   ${CONFIG.name}-hero.jpg`);
  } catch (e) {
    failed.push("hero");
    console.log(`FAIL ${CONFIG.name}-hero.jpg: ${e.message.split("\n")[0]}`);
  }
  return { failed, heroOk };
}

async function main(siteDir, outDir) {
  const server = await startServer(siteDir, siteBase(siteDir));
  const browser = await chromium.launch();
  try {
    const { heroOk } = await capture(browser, server.url, outDir);
    return heroOk ? 0 : 1;
  } finally {
    await browser.close();
    await server.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const args = process.argv.slice(2);
  const ci = args.indexOf("--config");
  const configFile = ci >= 0 ? args.splice(ci, 2)[1] : "demo/demo.config.json";
  const [siteDir, outDir] = args;
  if (!siteDir || !outDir) {
    console.error("usage: node capture.mjs <siteDir> <outDir> [--config demo/demo.config.json]");
    process.exit(2);
  }
  configure(readConfig(configFile));
  main(siteDir, outDir).then((code) => process.exit(code), (e) => {
    console.error(`ERROR ${e.stack || e.message}`);
    process.exit(1);
  });
}
