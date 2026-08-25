import type { ClientCommand, ServerEnvelope } from "./protocol";

export type ConnectionState = "connecting" | "connected" | "disconnected" | "reconnecting";

export interface WebSocketClientOptions {
  url: string;
  onStateChange: (state: ConnectionState) => void;
  onMessage: (message: ServerEnvelope) => void;
  onConnected?: () => void;
  onError?: (error: Error) => void;
  getSessionId?: () => string | null;
}

export class MessengerWebSocket {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private reconnectAttempt = 0;
  private autoReconnect = false;
  private manuallyClosed = false;
  private connectPromise: Promise<void> | null = null;
  private resolveConnect: (() => void) | null = null;
  private rejectConnect: ((error: Error) => void) | null = null;
  private readonly options: WebSocketClientOptions;

  constructor(options: WebSocketClientOptions) {
    this.options = options;
  }

  get url(): string {
    return this.options.url;
  }

  get readyState(): number {
    return this.socket?.readyState ?? WebSocket.CLOSED;
  }

  setAutoReconnect(enabled: boolean): void {
    this.autoReconnect = enabled;
    if (!enabled && this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  async connect(): Promise<void> {
    this.manuallyClosed = false;
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.setState("connected");
      return;
    }
    this.setState(this.reconnectAttempt > 0 ? "reconnecting" : "connecting");
    if (this.connectPromise) return this.connectPromise;

    this.connectPromise = new Promise<void>((resolve, reject) => {
      this.resolveConnect = resolve;
      this.rejectConnect = reject;
      this.openSocket();
    });
    return this.connectPromise;
  }

  send(command: ClientCommand, fields: Record<string, unknown> = {}): void {
    if (this.socket?.readyState !== WebSocket.OPEN) {
      throw new Error("O servidor não está conectado.");
    }
    this.socket.send(JSON.stringify({ command, ...fields }));
  }

  close(): void {
    this.manuallyClosed = true;
    this.autoReconnect = false;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.rejectPendingConnect(new Error("Conexão encerrada."));
    if (this.socket && this.socket.readyState !== WebSocket.CLOSED) {
      this.socket.close();
    }
    this.socket = null;
    this.setState("disconnected");
  }

  private openSocket(): void {
    try {
      this.socket = new WebSocket(this.options.url);
    } catch (error) {
      this.failConnect(error instanceof Error ? error : new Error("Não foi possível conectar."));
      return;
    }

    this.socket.addEventListener("open", () => {
      this.reconnectAttempt = 0;
      this.setState("connected");
      this.resolvePendingConnect();
      this.options.onConnected?.();
    });

    this.socket.addEventListener("message", (event) => {
      try {
        const parsed: unknown = JSON.parse(String(event.data));
        if (
          parsed &&
          typeof parsed === "object" &&
          "type" in parsed &&
          "payload" in parsed
        ) {
          this.options.onMessage(parsed as ServerEnvelope);
        } else {
          this.options.onError?.(new Error("Resposta inválida do servidor."));
        }
      } catch {
        this.options.onError?.(new Error("O servidor enviou uma resposta inválida."));
      }
    });

    this.socket.addEventListener("error", () => {
      this.options.onError?.(new Error("Não foi possível comunicar com o servidor."));
    });

    this.socket.addEventListener("close", () => {
      this.socket = null;
      this.setState("disconnected");
      this.rejectPendingConnect(new Error("A conexão com o servidor foi encerrada."));
      if (!this.manuallyClosed && this.autoReconnect && this.options.getSessionId?.()) {
        this.scheduleReconnect();
      }
    });
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;
    this.reconnectAttempt += 1;
    const delay = Math.min(1000 * 2 ** (this.reconnectAttempt - 1), 10000);
    this.setState("reconnecting");
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect().catch(() => {
        if (!this.manuallyClosed && this.autoReconnect && this.options.getSessionId?.()) {
          this.scheduleReconnect();
        }
      });
    }, delay);
  }

  private setState(state: ConnectionState): void {
    this.options.onStateChange(state);
  }

  private resolvePendingConnect(): void {
    this.resolveConnect?.();
    this.resolveConnect = null;
    this.rejectConnect = null;
    this.connectPromise = null;
  }

  private rejectPendingConnect(error: Error): void {
    this.rejectConnect?.(error);
    this.resolveConnect = null;
    this.rejectConnect = null;
    this.connectPromise = null;
  }

  private failConnect(error: Error): void {
    this.socket = null;
    this.setState("disconnected");
    this.options.onError?.(error);
    this.rejectPendingConnect(error);
    if (!this.manuallyClosed && this.autoReconnect && this.options.getSessionId?.()) {
      this.scheduleReconnect();
    }
  }
}
