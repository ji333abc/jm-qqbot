import assert from "node:assert/strict";
import test from "node:test";

import { classifyError, isRetryable, parseArgs, sendFileWithFallback } from "./uploader.mjs";

test("parses pairs of arguments", () => {
  assert.deepEqual(parseArgs(["--file", "/tmp/a.zip", "--name", "a.zip"]), {
    file: "/tmp/a.zip",
    name: "a.zip",
  });
});

test("classifies retryable failures", () => {
  assert.equal(classifyError(new Error("socket timeout")), "timeout");
  assert.equal(classifyError(Object.assign(new Error("msgid已经过期,不能回复"), { bizCode: 40034031 })), "expired");
  assert.equal(isRetryable("timeout"), true);
  assert.equal(isRetryable("auth"), false);
});

test("falls back to a proactive file message when msg_id expired", async () => {
  const calls = [];
  const bot = {
    async sendFile(target, source, options) {
      calls.push({ target, source, options });
      if (calls.length === 1) {
        throw Object.assign(new Error("msgid已经过期,不能回复"), { bizCode: 40034031 });
      }
      return { upload: { file_uuid: "uploaded" } };
    },
  };

  const result = await sendFileWithFallback(
    bot,
    { scope: "group", targetId: "group-openid", msgId: "expired-message" },
    { localPath: "/tmp/JM.zip" },
    { fileName: "JM.zip", content: "解压密码：test-password" },
    0,
  );

  assert.equal(result.upload.file_uuid, "uploaded");
  assert.deepEqual(calls.map((call) => call.target), [
      { scope: "group", targetId: "group-openid", msgId: "expired-message" },
      { scope: "group", targetId: "group-openid" },
    ]);
  assert.deepEqual(calls.map((call) => call.options.content), [
    "解压密码：test-password",
    "解压密码：test-password",
  ]);
});
