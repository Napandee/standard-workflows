// Exit-code semantics of capture.mjs: a failed desktop or mobile shot is
// logged and skipped; the hero is what decides success. Needs a browser: run in
// the Playwright image, e.g. run.sh test.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { capture, shoot, configure, readConfig, heroHtml } from "../capture.mjs";
import { launch } from "../check.mjs";

let browser;
let tmp;
before(async () => {
  configure({ name: "other", siteLabel: "example.test/Other" });
  browser = await launch();
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "capture-"));
});
after(async () => {
  await browser?.close();
  fs.rmSync(tmp, { recursive: true, force: true });
});

const fake = (failOn) => async (b, _url, options) => {
  if (failOn === (options.isMobile ? "mobile" : "desktop")) throw new Error("boom");
  const context = await b.newContext(options);
  const page = await context.newPage();
  await page.setContent("<body style='background:#345'>x</body>");
  const out = await page.screenshot({ type: "png" });
  await context.close();
  return out;
};

test("all shots good: three JPEGs, hero ok", async () => {
  const out = path.join(tmp, "all");
  const r = await capture(browser, "unused", out, { shoot: fake(null) });
  assert.deepEqual(r, { failed: [], heroOk: true });
  assert.deepEqual(fs.readdirSync(out).sort(), ["other-desktop.jpg", "other-hero.jpg", "other-mobile.jpg"]);
});

test("a failing mobile shot is skipped, and the hero fails with it", async () => {
  const out = path.join(tmp, "nomobile");
  const r = await capture(browser, "unused", out, { shoot: fake("mobile") });
  assert.deepEqual(r.failed, ["mobile", "hero"]);
  assert.equal(r.heroOk, false);
  assert.deepEqual(fs.readdirSync(out), ["other-desktop.jpg"]);
});

test("a failing hero composition leaves the other shots and reports it", async () => {
  const out = path.join(tmp, "nohero");
  const r = await capture(browser, "unused", out, {
    shoot: fake(null),
    compose: async () => { throw new Error("compose"); },
  });
  assert.deepEqual(r.failed, ["hero"]);
  assert.equal(r.heroOk, false);
  assert.ok(fs.existsSync(path.join(out, "other-desktop.jpg")));
  assert.ok(!fs.existsSync(path.join(out, "other-hero.jpg")));
});

test("shoot rejects a page that does not answer 200", async () => {
  await assert.rejects(shoot(browser, "data:text/html,hi", { viewport: { width: 100, height: 100 } }));
});

test("file names and the hero label come from the project config", () => {
  const html = heroHtml(Buffer.from("a"), Buffer.from("b"));
  assert.match(html, /example\.test\/Other/);
  assert.doesNotMatch(html, /AniDex/);
});

test("readConfig requires now, name and siteLabel", () => {
  const f = path.join(tmp, "cfg.json");
  fs.writeFileSync(f, JSON.stringify({ now: "2026-10-15T12:00:00Z", name: "x" }));
  assert.throws(() => readConfig(f), /"siteLabel" is required/);
  fs.writeFileSync(f, JSON.stringify({ now: "2026-10-15T12:00:00Z", name: "x", siteLabel: "x.test" }));
  assert.deepEqual(readConfig(f), { now: "2026-10-15T12:00:00Z", name: "x", siteLabel: "x.test" });
});
