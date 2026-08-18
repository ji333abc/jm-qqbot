import assert from "node:assert/strict";
import test from "node:test";

import { classifyError, isRetryable, parseArgs } from "./uploader.mjs";

test("parses pairs of arguments", () => {
  assert.deepEqual(parseArgs(["--file", "/tmp/a.zip", "--name", "a.zip"]), {
    file: "/tmp/a.zip",
    name: "a.zip",
  });
});

test("classifies retryable failures", () => {
  assert.equal(classifyError(new Error("socket timeout")), "timeout");
  assert.equal(isRetryable("timeout"), true);
  assert.equal(isRetryable("auth"), false);
});
