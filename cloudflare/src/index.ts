import { Container, getContainer } from "@cloudflare/containers";
import { env } from "cloudflare:workers";

type Env = {
  JM_BOT: DurableObjectNamespace<JMBotContainer>;
  QQBOT_APP_ID: string;
  QQBOT_APP_SECRET: string;
  QQBOT_ALLOWED_GROUP_OPENIDS: string;
  QQBOT_JM_ALLOWED_USER_OPENIDS: string;
  QQBOT_JM_BATCH_MAX_ITEMS: string;
  QQBOT_JM_MAX_BYTES: string;
  QQBOT_JM_TIMEOUT_SECONDS: string;
  QQBOT_JM_UPLOAD_TIMEOUT_SECONDS: string;
  QQBOT_JM_INSPECT_TIMEOUT_SECONDS: string;
  QQBOT_JM_FAILURE_RETAIN_SECONDS: string;
};

export class JMBotContainer extends Container {
  defaultPort = 8080;
  requiredPorts = [8080];
  pingEndpoint = "localhost/healthz";
  sleepAfter = "10m";
  enableInternet = true;
  envVars = {
    QQBOT_APP_ID: env.QQBOT_APP_ID,
    QQBOT_APP_SECRET: env.QQBOT_APP_SECRET,
    QQBOT_ALLOWED_GROUP_OPENIDS: env.QQBOT_ALLOWED_GROUP_OPENIDS,
    QQBOT_JM_ALLOWED_USER_OPENIDS: env.QQBOT_JM_ALLOWED_USER_OPENIDS,
    QQBOT_JM_BATCH_MAX_ITEMS: env.QQBOT_JM_BATCH_MAX_ITEMS,
    QQBOT_JM_MAX_BYTES: env.QQBOT_JM_MAX_BYTES,
    QQBOT_JM_TIMEOUT_SECONDS: env.QQBOT_JM_TIMEOUT_SECONDS,
    QQBOT_JM_UPLOAD_TIMEOUT_SECONDS: env.QQBOT_JM_UPLOAD_TIMEOUT_SECONDS,
    QQBOT_JM_INSPECT_TIMEOUT_SECONDS: env.QQBOT_JM_INSPECT_TIMEOUT_SECONDS,
    QQBOT_JM_FAILURE_RETAIN_SECONDS: env.QQBOT_JM_FAILURE_RETAIN_SECONDS,
  };
}

function bot(env: Env) {
  return getContainer(env.JM_BOT, "singleton");
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname !== "/" && url.pathname !== "/healthz") {
      return new Response("Not found", { status: 404 });
    }
    return bot(env).fetch(new Request("http://container/healthz"));
  },

  async scheduled(_controller: ScheduledController, env: Env): Promise<void> {
    const response = await bot(env).fetch(new Request("http://container/healthz"));
    await response.arrayBuffer();
  },
};
