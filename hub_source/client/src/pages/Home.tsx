/**
 * Direção visual preservada: Retro Desktop Leve — skeuomorfismo web do início
 * dos anos 2000, janela compacta, moldura azul, presença por ícones pequenos e
 * superfícies claras. Este arquivo contém apenas a composição da UI; rede e
 * estado do Messenger ficam em state/messenger.tsx e network/.
 */
import { ChangeEvent, FormEvent, ReactNode, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Bell,
  Check,
  ChevronDown,
  CircleHelp,
  Copy,
  KeyRound,
  LogIn,
  LogOut,
  MessageCircle,
  MoreHorizontal,
  Paperclip,
  Search,
  Send,
  Server,
  Settings2,
  ShieldCheck,
  Smile,
  UserPlus,
  UserRound,
  UsersRound,
  Wifi,
  X,
} from "lucide-react";
import {
  ChatMessage,
  Conversation,
  Friend,
  Identity,
  Status,
  useMessenger,
} from "@/state/messenger";
import type { ProfilePayload } from "@/network/protocol";
import type { ConnectionState } from "@/network/websocket";
import messengerMark from "@/assets/messenger-mark_663c8cf5.png";
import messengerOrbit from "@/assets/messenger-orbit_15ab62ba.png";
import messengerBadge from "@/assets/messenger-badge_e60698ec.png";

type ViewMode = "login" | "register" | "forgot" | "hub";

const statusCopy: Record<Status, string> = {
  online: "Online",
  away: "Ausente",
  busy: "Ocupado",
  offline: "Offline",
};

const statusNote: Record<Status, string> = {
  online: "Disponível para conversar",
  away: "Volto em alguns minutos",
  busy: "Não quero ser interrompido",
  offline: "Apareço como desconectado",
};

const statusOptions: Status[] = ["online", "away", "busy"];

function StatusDot({ status, className = "" }: { status: Status; className?: string }) {
  return <span aria-label={statusCopy[status]} className={`status-dot status-${status} ${className}`} />;
}

function Avatar({
  initials,
  color,
  status,
  group = false,
  avatarData,
}: {
  initials: string;
  color: string;
  status: Status;
  group?: boolean;
  avatarData?: string | null;
}) {
  return (
    <span className={`avatar ${group ? "avatar-group" : ""}`} style={{ backgroundColor: color }}>
      {group ? <UsersRound size={17} strokeWidth={1.8} /> : avatarData ? <img src={avatarData} alt="" /> : initials}
      <StatusDot status={status} />
    </span>
  );
}

function IconButton({
  label,
  onClick,
  children,
  active = false,
}: {
  label: string;
  onClick?: () => void;
  children: ReactNode;
  active?: boolean;
}) {
  return (
    <button
      className={`icon-button ${active ? "is-active" : ""}`}
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function WindowTopbar({
  title,
  subtitle,
  onClose,
}: {
  title: string;
  subtitle?: string;
  onClose?: () => void;
}) {
  return (
    <header className="window-topbar">
      <div className="window-title-wrap">
        <img className="window-title-mark" src={messengerMark} alt="" />
        <div>
          <p className="window-title">{title}</p>
          {subtitle && <p className="window-subtitle">{subtitle}</p>}
        </div>
      </div>
      <div className="window-tools">
        <span className="window-led" />
        {onClose && (
          <IconButton label="Voltar" onClick={onClose}>
            <X size={16} />
          </IconButton>
        )}
      </div>
    </header>
  );
}

function Field({
  label,
  icon,
  type = "text",
  placeholder,
  value,
  onChange,
}: {
  label: string;
  icon: ReactNode;
  type?: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <span className="field-control">
        <span className="field-icon">{icon}</span>
        <input
          type={type}
          placeholder={placeholder}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      </span>
    </label>
  );
}

function AuthView({
  mode,
  onLogin,
  onRegister,
  onForgot,
  onOpenRegister,
  onBack,
  busy,
  error,
  onClearError,
}: {
  mode: "login" | "register";
  onLogin: (username: string, password: string) => Promise<string | null>;
  onRegister: (username: string, displayName: string, password: string) => Promise<void>;
  onForgot: () => void;
  onOpenRegister?: () => void;
  onBack: () => void;
  busy: boolean;
  error: string | null;
  onClearError: () => void;
}) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const { serverUrl } = useMessenger();
  const isRegister = mode === "register";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLocalError(null);
    onClearError();
    if (!username.trim() || !password) {
      setLocalError("Informe seu nome de usuário e sua senha.");
      return;
    }
    if (isRegister) {
      if (!displayName.trim()) {
        setLocalError("Informe como você quer ser chamado.");
        return;
      }
      if (password !== confirmPassword) {
        setLocalError("A confirmação de senha não coincide.");
        return;
      }
      try {
        await onRegister(username, displayName, password);
      } catch (submitError) {
        setLocalError(submitError instanceof Error ? submitError.message : "Não foi possível criar a conta.");
      }
      return;
    }
    try {
      await onLogin(username, password);
    } catch (submitError) {
      setLocalError(submitError instanceof Error ? submitError.message : "Não foi possível entrar.");
    }
  }

  const visibleError = localError || error;

  return (
    <main className="app-stage auth-stage">
      <section className="messenger-window auth-window" aria-label={isRegister ? "Criar conta" : "Login do Messenger"}>
        <WindowTopbar
          title={isRegister ? "Criar conta" : "MSN Messenger"}
          subtitle="um mensageiro para gente próxima"
          onClose={isRegister ? onBack : undefined}
        />
        <div className="auth-content">
          <div className="auth-form-area">
            <div className="auth-brand-row">
              <img className="brand-mark" src={messengerMark} alt="Marca do Messenger" />
              <div>
                <p className="eyebrow">mensageiro privado</p>
                <h1>{isRegister ? "Vamos criar seu acesso." : "Seu grupo, ali pertinho."}</h1>
                <p className="auth-intro">
                  {isRegister
                    ? "Preencha seus dados para criar um acesso persistente no servidor."
                    : "Entre para ver suas conversas reais e continuar de onde parou."}
                </p>
              </div>
            </div>

            <form className="form-stack" onSubmit={(event) => void handleSubmit(event)}>
              <Field
                label="Nome de usuário"
                icon={<UserRound size={15} />}
                placeholder="ex.: seu_nome"
                value={username}
                onChange={setUsername}
              />
              {isRegister && (
                <Field
                  label="Como quer ser chamado"
                  icon={<UserRound size={15} />}
                  placeholder="ex.: Maria Clara"
                  value={displayName}
                  onChange={setDisplayName}
                />
              )}
              <Field
                label="Senha"
                icon={<KeyRound size={15} />}
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={setPassword}
              />
              {isRegister && (
                <Field
                  label="Confirmar senha"
                  icon={<ShieldCheck size={15} />}
                  type="password"
                  placeholder="Repita a senha"
                  value={confirmPassword}
                  onChange={setConfirmPassword}
                />
              )}
              {visibleError && (
                <div className="form-error" role="alert">
                  {visibleError}
                </div>
              )}
              <button className="primary-button" type="submit" disabled={busy}>
                {isRegister ? <UserPlus size={16} /> : <LogIn size={16} />}
                {busy ? "Aguarde..." : isRegister ? "Criar conta" : "Entrar"}
              </button>
            </form>

            <div className="auth-bottom-row">
              <button className="text-button" type="button" onClick={isRegister ? onBack : () => { onClearError(); onOpenRegister?.(); }}>
                {isRegister ? <><ArrowLeft size={14} /> Voltar ao login</> : "Criar uma conta"}
              </button>
              {!isRegister && <button className="text-button" type="button" onClick={() => { onClearError(); onForgot(); }}><KeyRound size={14} /> Esqueci minha senha</button>}
              <span className="mini-note"><Check size={13} /> dados mantidos no servidor</span>
            </div>
          </div>

          <aside className="auth-side" aria-label="Informações do servidor">
            <img className="auth-orbit" src={messengerOrbit} alt="" />
            <div className="auth-side-copy">
              <p className="side-kicker"><Wifi size={13} /> estado da conexão</p>
              <div className="connection-line"><StatusDot status="offline" /> <strong>Conecte para entrar</strong></div>
              <p>Servidor configurado</p>
              <code>{serverUrl}</code>
            </div>
            <img className="auth-badge" src={messengerBadge} alt="" />
          </aside>
        </div>
      </section>
    </main>
  );
}

function RecoveryCodeView({
  code,
  onContinue,
}: {
  code: string;
  onContinue: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);

  async function copyCode() {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(code);
      } else {
        const helper = document.createElement("textarea");
        helper.value = code;
        helper.setAttribute("readonly", "");
        helper.style.position = "fixed";
        helper.style.opacity = "0";
        document.body.appendChild(helper);
        helper.select();
        const didCopy = document.execCommand("copy");
        helper.remove();
        if (!didCopy) throw new Error("copy-failed");
      }
      setCopied(true);
      setCopyError(null);
    } catch {
      setCopied(false);
      setCopyError("Não foi possível copiar automaticamente. Anote o código antes de continuar.");
    }
  }

  return (
    <main className="app-stage auth-stage">
      <section className="messenger-window auth-window recovery-code-window" aria-label="Código de recuperação">
        <WindowTopbar title="Código de recuperação" subtitle="guarde este código com cuidado" />
        <div className="auth-content">
          <div className="auth-form-area">
            <div className="auth-brand-row">
              <img className="brand-mark" src={messengerMark} alt="Marca do Messenger" />
              <div>
                <p className="eyebrow">conta criada</p>
                <h1>Ei! Aqui está seu código.</h1>
                <p className="auth-intro">Ele será mostrado somente agora. Guarde-o em um lugar seguro para recuperar sua senha no futuro.</p>
              </div>
            </div>
            <div className="recovery-code-card">
              <span className="recovery-code-label">Seu código de recuperação</span>
              <strong className="recovery-code-value" aria-label="Código de recuperação">{code}</strong>
              <button className="small-button recovery-copy-button" type="button" onClick={() => void copyCode()}>
                <Copy size={13} /> {copied ? "Código copiado" : "Copiar código"}
              </button>
              {copied && <p className="inline-success" role="status">Código copiado para a área de transferência.</p>}
              {copyError && <p className="form-error" role="alert">{copyError}</p>}
            </div>
            <label className="recovery-code-confirm">
              <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
              <span>Já guardei meu código em um lugar seguro.</span>
            </label>
            <button className="primary-button recovery-continue-button" type="button" disabled={!confirmed} onClick={onContinue}>
              <Check size={16} /> Continuar
            </button>
            <p className="mini-note recovery-warning"><ShieldCheck size={13} /> se esta tela for fechada antes de guardar o código, ele não será mostrado novamente</p>
          </div>
          <aside className="auth-side" aria-label="Proteção do código">
            <img className="auth-orbit" src={messengerOrbit} alt="" />
            <div className="auth-side-copy">
              <p className="side-kicker"><ShieldCheck size={13} /> proteção da conta</p>
              <div className="connection-line"><StatusDot status="online" /> <strong>uma única exibição</strong></div>
              <p>O servidor guarda apenas o hash do código. Nem o código original nem a senha são salvos em texto puro.</p>
            </div>
            <img className="auth-badge" src={messengerBadge} alt="" />
          </aside>
        </div>
      </section>
    </main>
  );
}

function PasswordRecoveryView({
  onReset,
  onBack,
  busy,
  error,
  onClearError,
}: {
  onReset: (username: string, code: string, newPassword: string) => Promise<void>;
  onBack: () => void;
  busy: boolean;
  error: string | null;
  onClearError: () => void;
}) {
  const [username, setUsername] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  async function finishReset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLocalError(null);
    onClearError();
    if (!username.trim()) {
      setLocalError("Informe seu nome de usuário.");
      return;
    }
    if (!code.trim()) {
      setLocalError("Informe seu código de recuperação.");
      return;
    }
    if (newPassword.length < 6) {
      setLocalError("A nova senha deve ter no mínimo 6 caracteres.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setLocalError("A confirmação de senha não coincide.");
      return;
    }
    try {
      await onReset(username.trim(), code, newPassword);
      onBack();
    } catch (resetError) {
      setLocalError(resetError instanceof Error ? resetError.message : "Não foi possível trocar a senha.");
    }
  }

  const visibleError = localError || error;
  return (
    <main className="app-stage auth-stage">
      <section className="messenger-window auth-window" aria-label="Recuperar senha do Messenger">
        <WindowTopbar title="Recuperar acesso" subtitle="código local de recuperação" onClose={onBack} />
        <div className="auth-content">
          <div className="auth-form-area">
            <div className="auth-brand-row">
              <img className="brand-mark" src={messengerMark} alt="Marca do Messenger" />
              <div>
                <p className="eyebrow">recuperação segura</p>
                <h1>Use seu código de recuperação.</h1>
                <p className="auth-intro">Digite o username e o código que apareceram quando sua conta foi criada.</p>
              </div>
            </div>

            <form className="form-stack" onSubmit={(event) => void finishReset(event)}>
              <Field label="Nome de usuário" icon={<UserRound size={15} />} placeholder="ex.: seu_nome" value={username} onChange={setUsername} />
              <Field label="Código de recuperação" icon={<KeyRound size={15} />} placeholder="ex.: KAHEB7UA2M4Q9XCD" value={code} onChange={setCode} />
              <Field label="Nova senha" icon={<KeyRound size={15} />} type="password" placeholder="••••••••" value={newPassword} onChange={setNewPassword} />
              <Field label="Confirmar nova senha" icon={<ShieldCheck size={15} />} type="password" placeholder="Repita a senha" value={confirmPassword} onChange={setConfirmPassword} />
              {visibleError && <div className="form-error" role="alert">{visibleError}</div>}
              <button className="primary-button" type="submit" disabled={busy}><ShieldCheck size={16} /> {busy ? "Salvando..." : "Trocar senha"}</button>
              <p className="mini-note"><ShieldCheck size={13} /> sem e-mail e sem SMTP</p>
            </form>
            <div className="auth-bottom-row">
              <button className="text-button" type="button" onClick={onBack}><ArrowLeft size={14} /> Voltar ao login</button>
              <span className="mini-note"><Check size={13} /> uso único</span>
            </div>
          </div>
          <aside className="auth-side" aria-label="Recuperação de conta">
            <img className="auth-orbit" src={messengerOrbit} alt="" />
            <div className="auth-side-copy">
              <p className="side-kicker"><ShieldCheck size={13} /> proteção da conta</p>
              <div className="connection-line"><StatusDot status="away" /> <strong>código guardado por você</strong></div>
              <p>O MSN armazena somente uma representação protegida do código, nunca o código original.</p>
            </div>
            <img className="auth-badge" src={messengerBadge} alt="" />
          </aside>
        </div>
      </section>
    </main>
  );
}

function StatusSelector({ status, onChange }: { status: Status; onChange: (status: Status) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="status-selector">
      <button className="status-trigger" type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <StatusDot status={status} />
        <span>{statusCopy[status]}</span>
        <ChevronDown size={13} />
      </button>
      {open && (
        <div className="status-menu">
          {statusOptions.map((option) => (
            <button
              type="button"
              className={option === status ? "selected" : ""}
              key={option}
              onClick={() => {
                onChange(option);
                setOpen(false);
              }}
            >
              <StatusDot status={option} />
              <span>{statusCopy[option]}</span>
              {option === status && <Check size={13} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ConversationItem({ conversation, active, onClick }: { conversation: Conversation; active: boolean; onClick: () => void }) {
  return (
    <button className={`conversation-item ${active ? "is-active" : ""}`} type="button" onClick={onClick}>
      <Avatar initials={conversation.initials} color={conversation.color} status={conversation.status} group={conversation.kind === "group"} avatarData={conversation.avatarData} />
      <span className="conversation-copy">
        <span className="conversation-name-row"><strong>{conversation.name}</strong><time>{conversation.time}</time></span>
        <span className="conversation-preview">{conversation.customStatus || conversation.lastMessage}</span>
      </span>
    </button>
  );
}

function SettingsPanel({
  session,
  serverUrl,
  connectionState,
  status,
  onLogout,
  onClose,
  onAvatar,
  onCustomStatus,
  busy,
}: {
  session: Identity;
  serverUrl: string;
  connectionState: ConnectionState;
  status: Status;
  onLogout: () => void;
  onClose: () => void;
  onAvatar: (file: File) => Promise<void>;
  onCustomStatus: (message: string) => Promise<void>;
  busy: boolean;
}) {
  const [customStatus, setCustomStatus] = useState(session.custom_status || "");
  const fileRef = useRef<HTMLInputElement>(null);
  const connectionCopy: Record<ConnectionState, string> = {
    connecting: "conectando",
    connected: "conectado",
    disconnected: "desconectado",
    reconnecting: "reconectando",
  };
  const connectionStatus: Status = connectionState === "connected" ? "online" : connectionState === "disconnected" ? "offline" : "away";
  const ownInitials = (session.displayName || session.username).slice(0, 2).toUpperCase();
  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) await onAvatar(file);
  }
  async function saveStatus() {
    await onCustomStatus(customStatus.trim());
  }
  return (
    <aside className="settings-panel profile-panel" aria-label="Perfil e configurações">
      <div className="settings-heading"><div><span className="eyebrow">perfil</span><h2>Meu espaço</h2></div><IconButton label="Fechar configurações" onClick={onClose}><X size={15} /></IconButton></div>
      <div className="profile-editor">
        <div className="profile-avatar profile-avatar-small">{session.avatar_data ? <img src={session.avatar_data} alt="Foto de perfil" /> : ownInitials}<StatusDot status={status} /></div>
        <button className="text-button" type="button" onClick={() => fileRef.current?.click()} disabled={busy}>Alterar foto de perfil</button>
        <input ref={fileRef} className="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void upload(event)} />
      </div>
      <div className="settings-list">
        <div className="settings-row"><Server size={15} /><span><small>Endereço do servidor</small><strong>{serverUrl}</strong></span></div>
        <div className="settings-row"><Wifi size={15} /><span><small>Estado real</small><strong className={connectionState === "connected" ? "green-text" : ""}><StatusDot status={connectionStatus} /> {connectionCopy[connectionState]}</strong></span></div>
        <div className="settings-row"><UserRound size={15} /><span><small>Nome de usuário</small><strong>{session.username}</strong></span></div>
        <div className="settings-row"><ShieldCheck size={15} /><span><small>Nome exibido</small><strong>{session.displayName}</strong></span></div>
      </div>
      <label className="field compact-field"><span className="field-label">Status personalizado</span><input value={customStatus} maxLength={200} onChange={(event) => setCustomStatus(event.target.value)} placeholder="ex.: Construindo meu MSN" /></label>
      <div className="profile-actions"><button className="primary-button" type="button" onClick={() => void saveStatus()} disabled={busy}>Salvar status</button><button className="text-button" type="button" onClick={() => { setCustomStatus(""); void onCustomStatus(""); }} disabled={busy}>Limpar</button></div>
      <button className="logout-button" type="button" onClick={onLogout} disabled={busy}><LogOut size={15} /> {busy ? "Saindo..." : "Sair do Messenger"}</button>
    </aside>
  );
}

function friendInitials(user: { display_name: string; username: string }): string {
  return (user.display_name || user.username).slice(0, 2).toUpperCase();
}

function FriendsPanel({
  friends,
  searchUsers,
  sendFriendRequest,
  respondFriendRequest,
  removeFriend,
  openConversation,
  onClose,
  busy,
}: {
  friends: Friend[];
  searchUsers: (query: string) => Promise<ProfilePayload[]>;
  sendFriendRequest: (username: string) => Promise<void>;
  respondFriendRequest: (friendshipId: string, action: "accept" | "decline") => Promise<void>;
  removeFriend: (friendshipId: string) => Promise<void>;
  openConversation: (username: string) => Promise<void>;
  onClose: () => void;
  busy: boolean;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ProfilePayload[]>([]);
  const [searching, setSearching] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const accepted = friends.filter((friend) => friend.friendship_status === "accepted");
  const pending = friends.filter((friend) => friend.friendship_status === "pending");
  async function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) { setResults([]); return; }
    setSearching(true);
    try { setResults(await searchUsers(query.trim())); } finally { setSearching(false); }
  }
  async function add(username: string) {
    await sendFriendRequest(username);
    setMessage(`Solicitação enviada para @${username}.`);
    setResults((items) => items.filter((item) => item.username !== username));
  }
  async function respond(friend: Friend, action: "accept" | "decline") {
    await respondFriendRequest(friend.friendship_id, action);
  }
  return (
    <aside className="settings-panel friends-panel" aria-label="Amigos">
      <div className="settings-heading"><div><span className="eyebrow">contatos</span><h2>Amigos</h2></div><IconButton label="Fechar amigos" onClick={onClose}><X size={15} /></IconButton></div>
      <form className="friend-search" onSubmit={(event) => void submitSearch(event)}>
        <div className="search-box"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Pesquisar por username" aria-label="Pesquisar usuário" /></div>
        <button className="primary-button" type="submit" disabled={searching}>{searching ? "Buscando..." : "Pesquisar"}</button>
      </form>
      {message && <div className="inline-success" role="status">{message}</div>}
      {results.length > 0 && <div className="friend-section"><span className="friend-section-title">Resultados</span>{results.map((user) => <div className="friend-row" key={user.user_id}><Avatar initials={friendInitials({ display_name: user.display_name, username: user.username })} color="#84b9d8" status="offline" avatarData={user.avatar_data} /><span className="friend-copy"><strong>{user.display_name}</strong><small>@{user.username}</small></span><button className="small-button" type="button" onClick={() => void add(user.username)}>Adicionar</button></div>)}</div>}
      <div className="friend-section"><span className="friend-section-title">Solicitações pendentes <b>{pending.length}</b></span>{pending.length === 0 ? <p className="empty-panel-note">Nenhuma solicitação pendente.</p> : pending.map((friend) => <div className="friend-row" key={friend.friendship_id}><Avatar initials={friendInitials(friend)} color="#d8b4a1" status={friend.status} avatarData={friend.avatar_data} /><span className="friend-copy"><strong>{friend.display_name}</strong><small>{friend.incoming ? "quer ser seu amigo" : "aguardando resposta"}</small></span>{friend.incoming ? <span className="friend-actions"><button className="small-button" type="button" onClick={() => void respond(friend, "accept")}>Aceitar</button><button className="small-button muted" type="button" onClick={() => void respond(friend, "decline")}>Recusar</button></span> : <span className="friend-waiting">Pendente</span>}</div>)}</div>
      <div className="friend-section"><span className="friend-section-title">Minha lista <b>{accepted.length}</b></span>{accepted.length === 0 ? <p className="empty-panel-note">Você ainda não tem amigos.</p> : accepted.map((friend) => <div className="friend-row" key={friend.friendship_id}><Avatar initials={friendInitials(friend)} color="#b7c58b" status={friend.status} avatarData={friend.avatar_data} /><span className="friend-copy"><strong>{friend.display_name}</strong><small><StatusDot status={friend.status} /> {statusCopy[friend.status]}{friend.custom_status ? ` · ${friend.custom_status}` : ""}</small></span><span className="friend-actions"><button className="small-button" type="button" onClick={() => void openConversation(friend.username)}>Conversar</button><button className="small-button muted" type="button" onClick={() => void removeFriend(friend.friendship_id)}>Remover</button></span></div>)}</div>
    </aside>
  );
}

function GroupComposer({ onCreate, onClose, busy }: { onCreate: (name: string, participants: string[]) => Promise<void>; onClose: () => void; busy: boolean }) {
  const [name, setName] = useState("");
  const [participants, setParticipants] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const usernames = participants.split(",").map((value) => value.trim()).filter(Boolean);
    if (!name.trim()) { setValidationError("Informe um nome para o grupo."); return; }
    if (usernames.length === 0) { setValidationError("Informe pelo menos um participante."); return; }
    setValidationError(null);
    try {
      await onCreate(name.trim(), usernames);
      onClose();
    } catch {
      // The provider already exposes the server error in the Hub banner.
    }
  }
  return (
    <form className="group-composer" onSubmit={(event) => void submit(event)}>
      <div className="settings-heading"><div><span className="eyebrow">nova conversa</span><h2>Criar grupo</h2></div><IconButton label="Fechar" onClick={onClose}><X size={15} /></IconButton></div>
      <Field label="Nome do grupo" icon={<UsersRound size={15} />} placeholder="ex.: Equipe" value={name} onChange={setName} />
      <Field label="Participantes" icon={<UserPlus size={15} />} placeholder="nomes separados por vírgula" value={participants} onChange={setParticipants} />
      {validationError && <div className="form-error" role="alert">{validationError}</div>}
      <button className="primary-button" type="submit" disabled={busy}><UsersRound size={15} /> {busy ? "Criando..." : "Criar grupo"}</button>
    </form>
  );
}

function ChatEmpty({ onNewGroup }: { onNewGroup: () => void }) {
  return (
    <section className="chat-pane chat-empty" aria-label="Nenhuma conversa selecionada">
      <div className="empty-chat-content">
        <MessageCircle size={36} strokeWidth={1.4} />
        <h2>Sem conversas por enquanto</h2>
        <p>As conversas e mensagens aparecem aqui quando existirem no servidor.</p>
        <button className="primary-button" type="button" onClick={onNewGroup}><UsersRound size={15} /> Criar um grupo</button>
      </div>
    </section>
  );
}

function ChatView({ conversation, onSend, onLoadHistory }: { conversation: Conversation; onSend: (text: string) => Promise<void>; onLoadHistory: (id: string) => Promise<void> }) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [emojiOpen, setEmojiOpen] = useState(false);
  const [attachmentNote, setAttachmentNote] = useState<string | null>(null);

  async function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.trim() || sending) return;
    setSending(true);
    try {
      await onSend(draft.trim());
      setDraft("");
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="chat-pane" aria-label={`Conversa com ${conversation.name}`}>
      <header className="chat-header">
        <div className="chat-person"><Avatar initials={conversation.initials} color={conversation.color} status={conversation.status} group={conversation.kind === "group"} avatarData={conversation.avatarData} /><div><h2>{conversation.name}</h2><p><StatusDot status={conversation.status} /> {statusCopy[conversation.status]}</p>{conversation.customStatus && <small className="chat-custom-status">{conversation.customStatus}</small>}</div></div>
        <div className="chat-actions"><IconButton label="Atualizar histórico" onClick={() => void onLoadHistory(conversation.id)}><Search size={16} /></IconButton><span className="chat-more-wrap"><IconButton label="Mais opções" onClick={() => setMoreOpen((open) => !open)} active={moreOpen}><MoreHorizontal size={17} /></IconButton>{moreOpen && <span className="chat-menu"><button type="button" onClick={() => void onLoadHistory(conversation.id)}>Atualizar histórico</button><button type="button" onClick={() => { void navigator.clipboard?.writeText(conversation.id); setMoreOpen(false); }}>Copiar ID da conversa</button></span>}</span></div>
      </header>
      <div className="chat-context"><MessageCircle size={14} /><span>histórico sincronizado do servidor</span><span className="context-line" /></div>
      <div className="chat-messages">
        {conversation.messages.length === 0 ? (
          <div className="empty-message-note">Nenhuma mensagem nesta conversa ainda.</div>
        ) : conversation.messages.map((message: ChatMessage) => (
          <div className={`message-row ${message.author === "me" ? "mine" : ""}`} key={message.id}>
            <div className="message-meta"><strong>{message.author === "me" ? "Você" : message.authorName}</strong><time>{message.time}</time></div>
            <div className="message-bubble">{message.text}</div>
          </div>
        ))}
      </div>
      {attachmentNote && <div className="attachment-note" role="status">{attachmentNote}<button type="button" onClick={() => setAttachmentNote(null)} aria-label="Fechar aviso"><X size={12} /></button></div>}
      <form className="message-composer" onSubmit={(event) => void submitMessage(event)}>
        <div className="composer-tools"><IconButton label="Anexar arquivo" onClick={() => setAttachmentNote("Anexos ainda não fazem parte do protocolo de mensagens deste Hub.")}><Paperclip size={15} /></IconButton><span className="emoji-wrap"><IconButton label="Adicionar emoji" onClick={() => setEmojiOpen((open) => !open)} active={emojiOpen}><Smile size={15} /></IconButton>{emojiOpen && <span className="emoji-picker">{["😀", "😂", "😉", "❤️", "👍", "🎸", "🌙", "✨"].map((emoji) => <button type="button" key={emoji} onClick={() => { setDraft((current) => `${current}${emoji}`); setEmojiOpen(false); }}>{emoji}</button>)}</span>}</span></div>
        <input aria-label="Digite uma mensagem" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Digite uma mensagem..." disabled={sending} />
        <button className="send-button" type="submit" disabled={sending}><Send size={15} /> {sending ? "Enviando" : "Enviar"}</button>
      </form>
    </section>
  );
}

function Hub() {
  const {
    session,
    connectionState,
    serverUrl,
    error,
    busy,
    status,
    conversations,
    friends,
    logout,
    changeStatus,
    sendMessage,
    requestHistory,
    createGroup,
    searchUsers,
    sendFriendRequest,
    respondFriendRequest,
    removeFriend,
    openConversation,
    setAvatar,
    setCustomStatus,
    reconnectNow,
    clearError,
  } = useMessenger();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [friendsOpen, setFriendsOpen] = useState(false);
  const [groupOpen, setGroupOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const visibleConversations = useMemo(
    () => conversations.filter((conversation) => conversation.name.toLowerCase().includes(searchTerm.trim().toLowerCase())),
    [conversations, searchTerm],
  );
  const selectedConversation = visibleConversations.find((conversation) => conversation.id === selectedId) || visibleConversations[0] || null;
  const activeId = selectedConversation?.id || null;
  const connectionLabel: Record<ConnectionState, string> = {
    connected: "Conectado",
    connecting: "Conectando...",
    disconnected: "Desconectado",
    reconnecting: "Reconectando...",
  };
  const connectionStatus: Status = connectionState === "connected" ? "online" : connectionState === "disconnected" ? "offline" : "away";
  const ownInitials = session ? (session.displayName || session.username).slice(0, 2).toUpperCase() : "??";

  if (!session) return null;

  async function handleStatus(nextStatus: Status) {
    await changeStatus(nextStatus);
  }
  function openSettings() {
    setSettingsOpen(true);
    setFriendsOpen(false);
    setGroupOpen(false);
  }
  function openFriends() {
    setFriendsOpen(true);
    setSettingsOpen(false);
    setGroupOpen(false);
  }
  function openGroup() {
    setGroupOpen(true);
    setSettingsOpen(false);
    setFriendsOpen(false);
  }

  return (
    <main className="app-stage hub-stage">
      <section className="messenger-window hub-window" aria-label="Hub do MSN Messenger">
        <WindowTopbar title="MSN Messenger Hub" subtitle="grupo privado / dados reais" />
        {error && (
          <div className="hub-error" role="alert"><span>{error}</span><button type="button" onClick={clearError} aria-label="Fechar aviso"><X size={14} /></button></div>
        )}
        <div className="hub-body">
          <aside className="profile-rail">
            <div className="rail-profile">
              <div className="profile-avatar">{session.avatar_data ? <img src={session.avatar_data} alt="Foto de perfil" /> : ownInitials}<StatusDot status={status} /></div>
              <div className="profile-name"><strong>{session.displayName}</strong><span>@{session.username}</span></div>
              <StatusSelector status={status} onChange={(nextStatus) => void handleStatus(nextStatus)} />
              <p className="status-description">{session.custom_status || statusNote[status]}</p>
            </div>
            <div className="rail-divider" />
            <nav className="rail-nav" aria-label="Atalhos">
              <button type="button" className={`rail-nav-item ${!friendsOpen && !settingsOpen ? "active" : ""}`} onClick={() => { setFriendsOpen(false); setSettingsOpen(false); }}><MessageCircle size={15} /><span>Conversas</span><b>{conversations.length}</b></button>
              <button type="button" className={`rail-nav-item ${friendsOpen ? "active" : ""}`} onClick={openFriends}><UsersRound size={15} /><span>Amigos</span><b>{friends.filter((friend) => friend.friendship_status === "accepted").length}</b></button>
              <button type="button" className={`rail-nav-item ${settingsOpen ? "active" : ""}`} onClick={openSettings}><Settings2 size={15} /><span>Perfil</span></button>
              <button type="button" className="rail-nav-item" onClick={() => void reconnectNow()} disabled={connectionState === "connected"}><Wifi size={15} /><span>{connectionState === "connected" ? "Conexão ativa" : "Reconectar"}</span></button>
            </nav>
            <div className="rail-footer">
              <div className="rail-server"><Server size={14} /><span><small>servidor</small><strong>{serverUrl}</strong></span></div>
              <button className="rail-logout" type="button" onClick={() => void logout()} disabled={busy}><LogOut size={14} /> Sair</button>
            </div>
            {settingsOpen && <SettingsPanel session={session} serverUrl={serverUrl} connectionState={connectionState} status={status} onLogout={() => void logout()} onClose={() => setSettingsOpen(false)} onAvatar={setAvatar} onCustomStatus={setCustomStatus} busy={busy} />}
            {friendsOpen && <FriendsPanel friends={friends} searchUsers={searchUsers} sendFriendRequest={sendFriendRequest} respondFriendRequest={respondFriendRequest} removeFriend={removeFriend} openConversation={openConversation} onClose={() => setFriendsOpen(false)} busy={busy} />}
            {groupOpen && <GroupComposer onCreate={createGroup} onClose={() => setGroupOpen(false)} busy={busy} />}
          </aside>

          <section className="conversation-pane" aria-label="Conversas">
            <div className="pane-heading"><div><span className="eyebrow">seus contatos</span><h1>Conversas</h1></div><button className="new-chat-button" type="button" title="Novo grupo" onClick={openGroup}><UserPlus size={15} /></button></div>
            <div className="search-box"><Search size={14} /><input placeholder="Procurar contato" aria-label="Procurar contato" value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} /></div>
            <div className="conversation-section-label"><span>Recentes</span><span className="section-count">{visibleConversations.length}</span></div>
            <div className="conversation-list">
              {visibleConversations.length === 0 ? (
                <div className="empty-list-note">Nenhuma conversa encontrada.</div>
              ) : visibleConversations.map((conversation) => <ConversationItem key={conversation.id} conversation={conversation} active={conversation.id === activeId} onClick={() => setSelectedId(conversation.id)} />)}
            </div>
            <div className="conversation-footnote"><Bell size={14} /><span>Contatos e conversas vêm do servidor.</span></div>
          </section>

          {selectedConversation ? (
            <ChatView conversation={selectedConversation} onSend={(text) => sendMessage(selectedConversation.id, text)} onLoadHistory={requestHistory} />
          ) : <ChatEmpty onNewGroup={openGroup} />}
        </div>
        <footer className="connection-bar">
          <button type="button" className="connection-status" onClick={() => void reconnectNow()} disabled={connectionState === "connected"}><StatusDot status={connectionStatus} /><span>{connectionLabel[connectionState]}</span></button>
          <span className="connection-separator" />
          <span className="connection-server"><Server size={13} /> {serverUrl}</span>
          <span className="connection-spacer" />
          <span className="connection-help"><CircleHelp size={13} /> Messenger Hub · dados do servidor</span>
        </footer>
      </section>
    </main>
  );
}

export default function Home() {
  const [view, setView] = useState<ViewMode>("login");
  const [recoveryCode, setRecoveryCode] = useState<string | null>(null);
  const { session, login, register, resetPassword, error, busy, clearError } = useMessenger();

  if (recoveryCode) {
    return <RecoveryCodeView code={recoveryCode} onContinue={() => setRecoveryCode(null)} />;
  }
  if (session) return <Hub />;
  if (view === "forgot") {
    return (
      <PasswordRecoveryView
        busy={busy}
        error={error}
        onClearError={clearError}
        onReset={resetPassword}
        onBack={() => { clearError(); setView("login"); }}
      />
    );
  }
  if (view === "register") {
    return (
      <AuthView
        mode="register"
        busy={busy}
        error={error}
        onClearError={clearError}
        onLogin={login}
        onForgot={() => setView("forgot")}
        onRegister={async (username, displayName, password) => {
          const code = await register(username, displayName, password);
          setRecoveryCode(code);
        }}
        onBack={() => { clearError(); setView("login"); }}
      />
    );
  }
  return (
    <AuthView
      mode="login"
      busy={busy}
      error={error}
      onClearError={clearError}
      onLogin={async (username, password) => {
        const code = await login(username, password);
        if (code) {
          setRecoveryCode(code);
        } else {
          setView("hub");
        }
        return code;
      }}
      onForgot={() => setView("forgot")}
      onOpenRegister={() => setView("register")}
      onRegister={async () => { clearError(); setView("register"); }}
      onBack={() => setView("login")}
    />
  );
}
