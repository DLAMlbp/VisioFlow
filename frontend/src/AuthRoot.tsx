import { FormEvent, useEffect, useState } from "react";
import { Loader2, LockKeyhole, Plus, ShieldCheck, UserPlus, UserRound, Users, X } from "lucide-react";

import App from "./App";
import brandLogo from "./assets/image-processing-logo.svg";
import {
  ApiError,
  api,
  clearAuthenticationSession,
  setAuthenticationRequiredHandler
} from "./services/api";
import type { AuthSession, AuthUser, UserRole } from "./types";

export default function AuthRoot() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [managingUsers, setManagingUsers] = useState(false);

  useEffect(() => {
    let active = true;
    setAuthenticationRequiredHandler(() => {
      if (active) setSession(null);
    });
    void api.getCurrentSession()
      .then((value) => {
        if (active) setSession(value);
      })
      .catch((reason: unknown) => {
        if (active && (!(reason instanceof ApiError) || reason.status !== 401)) {
          setError(reason instanceof Error ? reason.message : "无法读取登录状态");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      setAuthenticationRequiredHandler(null);
    };
  }, []);

  async function handleLogin(username: string, password: string) {
    setError(null);
    const nextSession = await api.login(username, password);
    setSession(nextSession);
  }

  async function handleRegister(payload: {
    username: string;
    display_name: string;
    password: string;
  }) {
    setError(null);
    const nextSession = await api.register(payload);
    setSession(nextSession);
  }

  async function handleLogout() {
    try {
      await api.logout();
      setSession(null);
      setManagingUsers(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "退出登录失败");
    }
  }

  if (loading) return <AuthLoading />;
  if (!session) {
    return <LoginScreen error={error} onLogin={handleLogin} onRegister={handleRegister} />;
  }

  return <>
    <App
      user={session.user}
      onLogout={() => void handleLogout()}
      onManageUsers={() => setManagingUsers(true)}
    />
    {managingUsers && session.user.role === "admin" && (
      <UserManager
        currentUser={session.user}
        onClose={() => setManagingUsers(false)}
        onCurrentUserReset={() => {
          clearAuthenticationSession();
          setManagingUsers(false);
          setSession(null);
        }}
      />
    )}
  </>;
}

function AuthLoading() {
  return <main className="auth-screen" aria-live="polite">
    <div className="auth-loading"><Loader2 className="spin" size={28} />正在验证登录状态</div>
  </main>;
}

function LoginScreen({
  error,
  onLogin,
  onRegister
}: {
  error: string | null;
  onLogin: (username: string, password: string) => Promise<void>;
  onRegister: (payload: {
    username: string;
    display_name: string;
    password: string;
  }) => Promise<void>;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      if (mode === "register") {
        if (password !== passwordConfirmation) {
          setSubmitError("两次输入的密码不一致");
          return;
        }
        await onRegister({ username, display_name: displayName, password });
      } else {
        await onLogin(username, password);
      }
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : mode === "login" ? "登录失败" : "注册失败");
    } finally {
      setSubmitting(false);
    }
  }

  function switchMode() {
    setMode((current) => current === "login" ? "register" : "login");
    setPassword("");
    setPasswordConfirmation("");
    setSubmitError(null);
  }

  return <main className="auth-screen">
    <section className="login-card" aria-labelledby="authTitle">
      <div className="login-brand"><img src={brandLogo} alt="" /><span><strong>VisioFlow</strong><small>图片智能处理工作台</small></span></div>
      <div className="login-heading">
        <span>{mode === "login" ? <LockKeyhole size={20} /> : <UserPlus size={20} />}</span>
        <div>
          <h1 id="authTitle">{mode === "login" ? "登录工作台" : "注册账号"}</h1>
          <p>{mode === "login" ? "使用账号或邮箱继续。" : "注册后将以操作员身份直接进入工作台。"}</p>
        </div>
      </div>
      {(submitError || error) && <p className="auth-error" role="alert">{submitError || error}</p>}
      <form className="login-form" onSubmit={submit}>
        <label>用户名或邮箱<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" autoFocus required minLength={3} maxLength={50} pattern={mode === "register" ? "[a-zA-Z0-9][a-zA-Z0-9._+@-]*" : undefined} /></label>
        {mode === "register" && <label>显示名称<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="name" required maxLength={120} /></label>}
        <label>密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={mode === "register" ? 10 : undefined} maxLength={128} /></label>
        {mode === "register" && <label>确认密码<input type="password" value={passwordConfirmation} onChange={(event) => setPasswordConfirmation(event.target.value)} autoComplete="new-password" required minLength={10} maxLength={128} /></label>}
        <button className="primary-button" type="submit" disabled={submitting}>{submitting && <Loader2 className="spin" size={17} />}{submitting ? (mode === "login" ? "正在登录" : "正在注册") : (mode === "login" ? "登录" : "注册并进入")}</button>
      </form>
      <div className="auth-mode-switch">
        <span>{mode === "login" ? "还没有账号？" : "已经有账号？"}</span>
        <button type="button" onClick={switchMode}>{mode === "login" ? "立即注册" : "返回登录"}</button>
      </div>
    </section>
  </main>;
}

function UserManager({
  currentUser,
  onClose,
  onCurrentUserReset
}: {
  currentUser: AuthUser;
  onClose: () => void;
  onCurrentUserReset: () => void;
}) {
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [resetUser, setResetUser] = useState<AuthUser | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [form, setForm] = useState({ username: "", display_name: "", password: "", role: "operator" as UserRole });

  useEffect(() => {
    let active = true;
    void api.listUsers()
      .then((value) => { if (active) setUsers(value); })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "读取账号失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function createUser(event: FormEvent) {
    event.preventDefault();
    setSavingId("new");
    setError(null);
    try {
      const created = await api.createUser(form);
      setUsers((current) => [...current, created]);
      setForm({ username: "", display_name: "", password: "", role: "operator" });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建账号失败");
    } finally {
      setSavingId(null);
    }
  }

  async function updateUser(user: AuthUser, changes: { role?: UserRole; is_active?: boolean }) {
    setSavingId(user.id);
    setError(null);
    try {
      const updated = await api.updateUser(user.id, changes);
      setUsers((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新账号失败");
    } finally {
      setSavingId(null);
    }
  }

  async function submitPasswordReset(event: FormEvent) {
    event.preventDefault();
    if (!resetUser) return;
    setSavingId(resetUser.id);
    setError(null);
    try {
      await api.resetUserPassword(resetUser.id, resetPassword);
      if (resetUser.id === currentUser.id) {
        onCurrentUserReset();
        return;
      }
      setResetUser(null);
      setResetPassword("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重置密码失败");
    } finally {
      setSavingId(null);
    }
  }

  return <div className="account-dialog-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="account-dialog" role="dialog" aria-modal="true" aria-labelledby="accountDialogTitle" onMouseDown={(event) => event.stopPropagation()}>
      <header><div><span>ACCESS CONTROL</span><h2 id="accountDialogTitle"><Users size={21} />账号管理</h2></div><button className="icon-button" type="button" aria-label="关闭账号管理" onClick={onClose}><X size={18} /></button></header>
      {error && <p className="auth-error" role="alert">{error}</p>}
      <form className="account-create-form" onSubmit={createUser}>
        <div><Plus size={18} /><span><strong>创建账号</strong><small>密码至少 10 位</small></span></div>
        <label>用户名或邮箱<input value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} required minLength={3} maxLength={50} pattern="[a-zA-Z0-9][a-zA-Z0-9._+@-]*" /></label>
        <label>显示名称<input value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} required maxLength={120} /></label>
        <label>初始密码<input type="password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} required minLength={10} maxLength={128} autoComplete="new-password" /></label>
        <label>角色<select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value as UserRole })}><option value="operator">操作员</option><option value="admin">管理员</option></select></label>
        <button className="primary-button" type="submit" disabled={savingId === "new"}>{savingId === "new" ? "创建中" : "创建账号"}</button>
      </form>
      <div className="account-list-heading"><strong>现有账号</strong><span>{users.filter((user) => user.is_active).length} 个已启用</span></div>
      {loading ? <div className="account-loading"><Loader2 className="spin" size={20} />正在读取账号</div> : <div className="account-list">
        {users.map((user) => <article key={user.id} className={!user.is_active ? "disabled" : ""}>
          <span className="account-avatar"><UserRound size={18} /></span>
          <div className="account-identity"><strong>{user.display_name}</strong><span>{user.username.includes("@") ? user.username : `@${user.username}`}{user.id === currentUser.id ? " · 当前账号" : ""}</span></div>
          <span className={`account-role ${user.role}`}><ShieldCheck size={14} />{user.role === "admin" ? "管理员" : "操作员"}</span>
          <div className="account-actions">
            <button type="button" disabled={savingId === user.id || user.id === currentUser.id} onClick={() => void updateUser(user, { role: user.role === "admin" ? "operator" : "admin" })}>设为{user.role === "admin" ? "操作员" : "管理员"}</button>
            <button type="button" disabled={savingId === user.id} onClick={() => { setResetUser(user); setResetPassword(""); }}>重置密码</button>
            <button type="button" disabled={savingId === user.id || user.id === currentUser.id} onClick={() => void updateUser(user, { is_active: !user.is_active })}>{user.is_active ? "停用" : "启用"}</button>
          </div>
        </article>)}
      </div>}
      {resetUser && <form className="password-reset-panel" onSubmit={submitPasswordReset}><div><strong>重置 {resetUser.display_name} 的密码</strong><small>保存后该账号的现有登录会立即失效。</small></div><input type="password" value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} placeholder="输入至少 10 位的新密码" minLength={10} maxLength={128} autoComplete="new-password" required autoFocus /><button className="primary-button" type="submit" disabled={savingId === resetUser.id}>保存新密码</button><button className="ghost-button" type="button" onClick={() => setResetUser(null)}>取消</button></form>}
    </section>
  </div>;
}
