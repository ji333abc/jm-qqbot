#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";
import { QQBot, UploadDailyLimitExceededError } from "@tencent-connect/qqbot-nodejs";

const PLATFORM_MAX_BYTES = 100 * 1024 * 1024;
const RETRY_DELAY_MS = 10_000;

const log = (level, message) => process.stderr.write(`[${level}] ${String(message)}\n`);
const logger = {
  debug: (message) => log("debug", message),
  info: (message) => log("info", message),
  warn: (message) => log("warn", message),
  error: (message) => log("error", message),
};

export function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) throw new Error(`无效参数: ${key ?? "<empty>"}`);
    args[key.slice(2)] = value;
  }
  return args;
}

function required(value, label) {
  const normalized = String(value ?? "").trim();
  if (!normalized) throw new Error(`缺少 ${label}`);
  return normalized;
}

function isWithin(filePath, rootPath) {
  const relative = path.relative(rootPath, filePath);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

export function validateFile(rawPath) {
  if (!path.isAbsolute(rawPath)) throw new Error("上传文件必须使用绝对路径");
  const realPath = fs.realpathSync(rawPath);
  const stat = fs.statSync(realPath);
  if (!stat.isFile()) throw new Error("上传目标不是普通文件");
  const configuredRoot = process.env.QQBOT_JM_TEMP_ROOT || "/app/data/jm-tasks";
  const rootPath = fs.existsSync(configuredRoot) ? fs.realpathSync(configuredRoot) : path.resolve(configuredRoot);
  if (!isWithin(realPath, rootPath)) throw new Error("上传文件不在 JM 临时目录中");
  const configuredLimit = Number.parseInt(process.env.QQBOT_JM_MAX_BYTES || String(80 * 1024 * 1024), 10);
  const limit = Number.isSafeInteger(configuredLimit) && configuredLimit > 0
    ? Math.min(configuredLimit, PLATFORM_MAX_BYTES)
    : 80 * 1024 * 1024;
  if (stat.size > limit) throw new RangeError(`文件超过 ${(limit / 1024 / 1024).toFixed(0)} MiB 上限`);
  return { realPath, size: stat.size };
}

function messageOf(error) {
  return (error instanceof Error ? error.message : String(error)).replace(/[\r\n]+/g, " ").slice(-500);
}

export function classifyError(error) {
  if (error instanceof UploadDailyLimitExceededError) return "quota";
  if (error instanceof RangeError) return "size";
  const message = messageOf(error).toLowerCase();
  const status = Number(error?.httpStatus || 0);
  const code = Number(error?.bizCode || 0);
  if (status === 401 || status === 403 || code === 11255 || /invalid.*(?:token|secret)|unauthori[sz]ed|forbidden|鉴权|认证/.test(message)) return "auth";
  if (/too large|file size|超过.*(?:mib|mb|大小)|entity too large/.test(message)) return "size";
  if (/timeout|timed out|aborterror|超时/.test(message)) return "timeout";
  if (status === 408 || status === 429 || status >= 500 || /network|fetch failed|econn|eai_again|socket|cos put failed|网络/.test(message)) return "network";
  return "api";
}

export const isRetryable = (type) => type === "network" || type === "timeout";
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export async function uploadWithRetry(bot, target, source, options, retryDelayMs = RETRY_DELAY_MS) {
  let lastError;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      return await bot.sendFile(target, source, options);
    } catch (error) {
      lastError = error;
      const type = classifyError(error);
      log("error", `第 ${attempt} 次上传失败 (${type}): ${messageOf(error)}`);
      if (attempt === 2 || !isRetryable(type)) throw error;
      await sleep(retryDelayMs);
    }
  }
  throw lastError;
}

async function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const appId = required(process.env.QQBOT_APP_ID, "QQBOT_APP_ID");
    const appSecret = required(process.env.QQBOT_APP_SECRET, "QQBOT_APP_SECRET");
    const groupOpenid = required(args["group-openid"], "--group-openid");
    const messageId = required(args["msg-id"], "--msg-id");
    const displayName = path.basename(required(args.name, "--name"));
    const { realPath, size } = validateFile(required(args.file, "--file"));
    log("info", `准备上传 ${displayName} (${(size / 1024 / 1024).toFixed(1)} MiB)`);
    const bot = new QQBot({ appId, appSecret, logger, userAgent: "jm-qqbot-uploader/0.1.0" });
    const result = await uploadWithRetry(
      bot,
      { scope: "group", targetId: groupOpenid, msgId: messageId },
      { localPath: realPath },
      {
        fileName: displayName,
        onProgress: (uploaded, total) => log("info", `上传进度 ${uploaded}/${total}`),
      },
    );
    process.stdout.write(`${JSON.stringify({ ok: true, fileUuid: result.upload.file_uuid || "", ttl: result.upload.ttl || 0 })}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({ ok: false, errorType: classifyError(error), message: messageOf(error) })}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
