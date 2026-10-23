"use client";

import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, CircleAlert, LoaderCircle, Send, Square, Volume2, VolumeX } from "lucide-react";

/** 数字人助手面板：左侧魔珐星云 3D 数字人，右侧 WebSocket 受控对话，回复自动朗读。 */

const SDK_URL = "https://media.xingyun3d.com/xingyun3d/general/litesdk/xmovAvatar@latest.js";
const DEFAULT_GATEWAY = "https://nebula-agent.xingyun3d.com/user/v1/ttsa/session";
const WS_PATH = "/api/v1/ws/chat";
const AVATAR_WIDTH = 300;
const AVATAR_HEIGHT = 480;

type AvatarPhase = "idle" | "connecting" | "downloading" | "ready" | "error" | "unconfigured";
type ChatRole = "user" | "bot" | "system";

interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
}

interface AvatarCredentials {
  app_id: string;
  app_secret: string;
  gateway_server: string;
}

interface XmovAvatarInstance {
  init: (options: {
    initModel: string;
    onDownloadProgress: (progress: number) => void;
  }) => void;
  speak: (text: string, isStart: boolean, isEnd: boolean) => void;
  interactiveidle: () => void;
  destroy: () => void;
}

declare global {
  interface Window {
    XmovAvatar?: new (options: Record<string, unknown>) => XmovAvatarInstance;
  }
}

interface DigitalHumanAssistantProps {
  workspaceId: number;
  products: Array<{ id: number; name: string }>;
  stores: Array<{ id: number; name: string }>;
}

/** 渲染 DigitalHumanAssistant 组件，把数字人渲染、SDK 连接与 WebSocket 对话收在一个面板里。 */
export function DigitalHumanAssistant({ workspaceId, products, stores }: DigitalHumanAssistantProps) {
  const [sdkLoaded, setSdkLoaded] = useState(false);
  const [avatarPhase, setAvatarPhase] = useState<AvatarPhase>("idle");
  const [progress, setProgress] = useState(0);
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [autoSpeak, setAutoSpeak] = useState(true);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [thinkingHint, setThinkingHint] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [inputText, setInputText] = useState("");
  const [productId, setProductId] = useState("");
  const [storeId, setStoreId] = useState("");

  const avatarRef = useRef<XmovAvatarInstance | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const autoSpeakRef = useRef(autoSpeak);
  autoSpeakRef.current = autoSpeak;

  const wsBaseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(
    /^http/,
    "ws",
  );

  const pushMessage = useCallback((role: ChatRole, text: string) => {
    setMessages((current) => [
      ...current,
      { id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, role, text },
    ]);
  }, []);

  const speak = useCallback((text: string) => {
    const sdk = avatarRef.current;
    if (!sdk || !text.trim()) return;
    try {
      // 连续播报之间先回待机，避免口型错位（官方建议）。
      sdk.interactiveidle();
      sdk.speak(text, true, true);
    } catch {
      // 单次播报失败不影响对话继续。
    }
  }, []);

  const handleFrame = useCallback(
    (frame: Record<string, unknown>) => {
      switch (frame.type) {
        case "status": {
          const status = String(frame.status ?? "");
          if (status === "thinking") {
            setThinkingHint(typeof frame.hint === "string" ? frame.hint : "正在思考…");
          } else {
            setThinkingHint(null);
          }
          if (status === "interrupted") pushMessage("system", "已打断上一轮回复。");
          if (status === "awaiting_review") {
            pushMessage("system", "草稿已生成，等待人工审核后继续执行。");
          }
          if (status === "cancelled") pushMessage("system", "本轮已取消。");
          break;
        }
        case "bot_text": {
          const text = typeof frame.text === "string" ? frame.text : "";
          setThinkingHint(null);
          if (text) {
            pushMessage("bot", text);
            if (autoSpeakRef.current) speak(text);
          }
          break;
        }
        case "error": {
          setThinkingHint(null);
          pushMessage("system", `出错了：${String(frame.message ?? "未知错误")}`);
          break;
        }
        case "turn_end": {
          setThinkingHint(null);
          break;
        }
        default:
          break;
      }
    },
    [pushMessage, speak],
  );

  const ensureConnection = useCallback(async (): Promise<WebSocket | null> => {
    const existing = wsRef.current;
    if (existing && existing.readyState === WebSocket.OPEN) return existing;
    let ticket: string;
    try {
      const response = await fetch("/api/chat/ws-ticket", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspaceId }),
      });
      if (!response.ok) throw new Error(`ticket ${response.status}`);
      ticket = ((await response.json()) as { ticket?: string }).ticket ?? "";
      if (!ticket) throw new Error("empty ticket");
    } catch {
      pushMessage("system", "获取连接票据失败：请确认已登录且具备当前 Workspace 的编辑权限。");
      return null;
    }
    const ws = new WebSocket(
      `${wsBaseUrl}${WS_PATH}?ticket=${encodeURIComponent(ticket)}&workspace_id=${workspaceId}`,
    );
    wsRef.current = ws;
    return new Promise((resolve) => {
      const settled = { done: false };
      const finish = (result: WebSocket | null) => {
        if (settled.done) return;
        settled.done = true;
        resolve(result);
      };
      ws.onopen = () => {
        setWsConnected(true);
        finish(ws);
      };
      ws.onmessage = (event) => {
        try {
          handleFrame(JSON.parse(event.data) as Record<string, unknown>);
        } catch {
          // 忽略无法解析的帧，连接保持可用。
        }
      };
      ws.onclose = () => {
        setWsConnected(false);
        if (wsRef.current === ws) wsRef.current = null;
        finish(null);
      };
      ws.onerror = () => finish(null);
    });
  }, [handleFrame, pushMessage, workspaceId, wsBaseUrl]);

  const connectAvatar = useCallback(async () => {
    setAvatarError(null);
    setAvatarPhase("connecting");
    let credentials: AvatarCredentials;
    try {
      const response = await fetch("/api/digital-human/credentials", { cache: "no-store" });
      if (!response.ok) throw new Error(`credentials ${response.status}`);
      credentials = (await response.json()) as AvatarCredentials;
    } catch {
      setAvatarPhase("error");
      setAvatarError("获取数字人凭证失败，请检查后端服务。");
      return;
    }
    if (!credentials.app_id || !credentials.app_secret) {
      setAvatarPhase("unconfigured");
      setAvatarError(
        "未配置数字人凭证：请在 api/.env 设置 DIGITAL_HUMAN_APP_ID 与 DIGITAL_HUMAN_APP_SECRET（xingyun3d.com 实名注册可领 300 免费分钟）。",
      );
      return;
    }
    if (!window.XmovAvatar) {
      setAvatarPhase("error");
      setAvatarError("数字人 SDK 尚未加载完成，请稍后重试。");
      return;
    }
    try {
      const instance = new window.XmovAvatar({
        containerId: "#digital-human-sdk",
        appId: credentials.app_id,
        appSecret: credentials.app_secret,
        gatewayServer: credentials.gateway_server || DEFAULT_GATEWAY,
        hardwareAcceleration: "prefer-hardware",
        enableLogger: true,
        onMessage: (message: unknown) => {
          const code = (message as { code?: number } | null)?.code;
          // 50001/50002 是在线/离线模式切换，属正常状态，不当错误展示。
          if (typeof code === "number" && code !== 50001 && code !== 50002) {
            setAvatarError(`SDK 消息 code=${code}`);
          }
        },
        onVoiceStateChange: (status: unknown) => {
          if (status === "start") setThinkingHint("数字人正在说话…");
        },
      });
      avatarRef.current = instance;
      instance.init({
        initModel: "normal",
        onDownloadProgress: (value: number) => {
          setProgress(value);
          if (value >= 100) {
            setAvatarPhase("ready");
            setAvatarError(null);
          }
        },
      });
      setAvatarPhase("downloading");
    } catch (error) {
      setAvatarPhase("error");
      setAvatarError(error instanceof Error ? error.message : "数字人初始化失败。");
    }
  }, []);

  const disconnectAvatar = useCallback(() => {
    try {
      avatarRef.current?.destroy();
    } catch {
      // 销毁异常忽略，状态照常复位。
    }
    avatarRef.current = null;
    setAvatarPhase("idle");
    setProgress(0);
  }, []);

  const send = useCallback(async () => {
    const text = inputText.trim();
    if (!text) return;
    if (!productId && !storeId) {
      pushMessage("system", "受控工作流需要上下文：请先选择商品或店铺再发送。");
      return;
    }
    pushMessage("user", text);
    setInputText("");
    const ws = await ensureConnection();
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      pushMessage("system", "WebSocket 连接失败，请稍后重试。");
      return;
    }
    ws.send(
      JSON.stringify({
        type: "user_text",
        text,
        context: {
          ...(productId ? { product_id: Number(productId) } : {}),
          ...(storeId ? { store_id: Number(storeId) } : {}),
        },
      }),
    );
  }, [ensureConnection, inputText, productId, pushMessage, storeId]);

  const cancelTurn = useCallback(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "cancel" }));
    }
  }, []);

  useEffect(() => {
    const handleUnload = () => {
      try {
        avatarRef.current?.destroy();
      } catch {
        // 页面卸载时忽略销毁异常。
      }
    };
    window.addEventListener("beforeunload", handleUnload);
    return () => {
      window.removeEventListener("beforeunload", handleUnload);
      handleUnload();
    };
  }, []);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  const avatarStatusText: Record<AvatarPhase, string> = {
    idle: "未连接",
    connecting: "正在获取凭证…",
    downloading: `资源下载中 ${progress}%`,
    ready: "就绪",
    error: "连接异常",
    unconfigured: "未配置凭证",
  };

  return (
    <section className="card p-5" id="digital-human">
      <Script src={SDK_URL} strategy="afterInteractive" onReady={() => setSdkLoaded(true)} />

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-[var(--primary)]">
            <Bot className="h-5 w-5" aria-hidden="true" />
            <span className="text-sm font-semibold">数字人运营助手</span>
          </div>
          <h2 className="mt-2 text-xl font-semibold text-[var(--foreground)]">
            看得见、听得到的受控对话
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--muted-foreground)]">
            左侧为魔珐星云 3D 数字人，右侧通过 WebSocket 与受控 Agent 任务链路实时对话；
            回复文本会同步由数字人朗读。密钥只存后端，页面不接触任何凭证明文。
          </p>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${
            wsConnected
              ? "bg-[var(--success-soft)] text-[var(--success)]"
              : "bg-[var(--muted)] text-[var(--muted-foreground)]"
          }`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${wsConnected ? "bg-[var(--success)]" : "bg-[var(--muted-foreground)]"}`} />
          {wsConnected ? "对话通道已连接" : "对话通道未连接"}
        </span>
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[300px_1fr]">
        <div className="grid content-start gap-3">
          <div
            className="relative overflow-hidden rounded-[12px] border border-[var(--border-strong)] bg-black"
            style={{ width: AVATAR_WIDTH, height: AVATAR_HEIGHT }}
          >
            <div id="digital-human-sdk" style={{ width: AVATAR_WIDTH, height: AVATAR_HEIGHT }} />
            {avatarPhase !== "ready" ? (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/80 px-6 text-center">
                {avatarPhase === "downloading" ? (
                  <>
                    <LoaderCircle className="h-6 w-6 animate-spin text-white/80" aria-hidden="true" />
                    <div className="h-1.5 w-full max-w-[220px] overflow-hidden rounded-full bg-white/20">
                      <div
                        className="h-full rounded-full bg-[var(--primary)] transition-all"
                        style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
                      />
                    </div>
                    <p className="text-xs text-white/70">数字人资源下载中 {progress}%</p>
                  </>
                ) : (
                  <>
                    <Bot className="h-8 w-8 text-white/50" aria-hidden="true" />
                    <p className="text-sm text-white/80">{avatarStatusText[avatarPhase]}</p>
                    <p className="text-xs leading-5 text-white/50">
                      点击下方按钮连接数字人；未连接时不消耗免费时长
                    </p>
                  </>
                )}
              </div>
            ) : null}
          </div>

          {avatarPhase === "ready" ? (
            <button
              type="button"
              onClick={disconnectAvatar}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-[10px] border border-[var(--danger)]/30 px-4 text-sm font-medium text-[var(--danger)] transition hover:bg-[var(--danger-soft)]"
            >
              <Square className="h-4 w-4" aria-hidden="true" />
              断开数字人
            </button>
          ) : (
            <button
              type="button"
              onClick={connectAvatar}
              disabled={!sdkLoaded || avatarPhase === "connecting" || avatarPhase === "downloading"}
              className="btn-primary inline-flex h-10 items-center justify-center gap-2 px-4 text-sm font-medium disabled:opacity-50"
            >
              {avatarPhase === "connecting" || avatarPhase === "downloading" ? (
                <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Volume2 className="h-4 w-4" aria-hidden="true" />
              )}
              {sdkLoaded ? "连接数字人" : "SDK 加载中…"}
            </button>
          )}

          <label className="flex cursor-pointer items-center gap-2 text-sm text-[var(--muted-foreground)]">
            <input
              type="checkbox"
              checked={autoSpeak}
              onChange={(event) => setAutoSpeak(event.target.checked)}
              className="h-4 w-4 rounded border-[var(--border-strong)]"
            />
            {autoSpeak ? <Volume2 className="h-4 w-4" aria-hidden="true" /> : <VolumeX className="h-4 w-4" aria-hidden="true" />}
            自动朗读回复
          </label>

          {avatarError ? (
            <div className="flex items-start gap-2 rounded-[10px] border border-[var(--warning)]/30 bg-[var(--warning-soft)] px-3 py-2 text-xs leading-5 text-[var(--warning)]">
              <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              {avatarError}
            </div>
          ) : null}
        </div>

        <div className="flex h-[480px] flex-col rounded-[12px] border border-[var(--border)] bg-[var(--muted)]">
          <div className="flex-1 space-y-3 overflow-auto p-4">
            {messages.length === 0 ? (
              <p className="py-16 text-center text-sm text-[var(--muted-foreground)]">
                选择商品或店铺上下文，开始和运营助手对话
              </p>
            ) : null}
            {messages.map((message) => (
              <div
                key={message.id}
                className={message.role === "user" ? "flex justify-end" : "flex justify-start"}
              >
                <div
                  className={`max-w-[80%] rounded-2xl px-4 py-2 text-sm leading-6 ${
                    message.role === "user"
                      ? "rounded-br-md bg-[var(--primary)] text-white"
                      : message.role === "system"
                        ? "rounded-bl-md bg-transparent text-xs text-[var(--muted-foreground)]"
                        : "rounded-bl-md border border-[var(--border)] bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                  }`}
                >
                  {message.text}
                </div>
              </div>
            ))}
            {thinkingHint ? (
              <div className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                {thinkingHint}
              </div>
            ) : null}
          </div>

          <div className="border-t border-[var(--border)] p-3">
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={productId}
                onChange={(event) => setProductId(event.target.value)}
                className="input h-9 min-w-[140px] flex-1 rounded-[10px] px-2 text-sm"
                aria-label="选择商品上下文"
              >
                <option value="">商品上下文</option>
                {products.map((product) => (
                  <option key={product.id} value={product.id}>
                    {product.name}
                  </option>
                ))}
              </select>
              <select
                value={storeId}
                onChange={(event) => setStoreId(event.target.value)}
                className="input h-9 min-w-[140px] flex-1 rounded-[10px] px-2 text-sm"
                aria-label="选择店铺上下文"
              >
                <option value="">店铺上下文</option>
                {stores.map((store) => (
                  <option key={store.id} value={store.id}>
                    {store.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="mt-2 flex items-end gap-2">
              <textarea
                value={inputText}
                onChange={(event) => setInputText(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void send();
                  }
                }}
                rows={2}
                placeholder="例如：检查这款商品近期的库存和经营风险，并给出下一步建议（Enter 发送，Shift+Enter 换行）"
                className="input min-h-[44px] flex-1 resize-none rounded-[10px] px-3 py-2 text-sm"
              />
              <button
                type="button"
                onClick={cancelTurn}
                className="btn-ghost inline-flex h-10 items-center gap-1.5 px-3 text-sm font-medium"
                title="取消当前生成"
              >
                <Square className="h-4 w-4" aria-hidden="true" />
                取消
              </button>
              <button
                type="button"
                onClick={() => void send()}
                className="btn-primary inline-flex h-10 items-center gap-1.5 px-4 text-sm font-medium"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                发送
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
