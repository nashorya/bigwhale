import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("./App.jsx", import.meta.url), "utf8");

test("visited pages remain mounted and are hidden instead of conditionally removed", () => {
  assert.match(appSource, /function PersistentPage\(/);
  assert.match(appSource, /visitedPages\.has\("chat"\)/);
  assert.match(appSource, /<PersistentPage active=\{page === "chat"\}>/);
  assert.doesNotMatch(appSource, /\{page === "chat" && \(\s*<Chat/);
});

test("chat state belongs to App and remounting Chat never resets the backend", () => {
  assert.match(appSource, /const \[chatSessions, setChatSessions\]/);
  assert.match(appSource, /getChatSessionId\(persona\.id\)/);
  assert.match(
    appSource,
    /sessionStorage\.setItem\(\s*chatStorageKey/,
  );
  assert.doesNotMatch(appSource, /fetch\("\/api\/chat\/reset"/);
  assert.doesNotMatch(appSource, /function Chat[\s\S]*?useState\(\[\]\)/);
});
