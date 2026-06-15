import { useEffect, useRef, useState } from "react";
import { PERSONAS, FEATURES, COMMANDS } from "./personas.js";

// ──────────────────────────────────────────
// 工具
// ──────────────────────────────────────────

function getSessionId() {
  let sid = sessionStorage.getItem("shore_session_id");
  if (!sid) {
    sid = crypto.randomUUID().replace(/-/g, "").slice(0, 32);
    sessionStorage.setItem("shore_session_id", sid);
  }
  return sid;
}

function chatStorageKey(personaId) {
  return `shore_chat_messages_${personaId}`;
}

function getChatSessionId(personaId) {
  return `${getSessionId()}:${personaId}`;
}

function createChatSession(persona) {
  let messages = [];
  try {
    const stored = JSON.parse(
      sessionStorage.getItem(chatStorageKey(persona.id)) || "[]",
    );
    if (Array.isArray(stored)) messages = stored.slice(-40);
  } catch {
    messages = [];
  }

  return {
    messages: messages.length
      ? messages
      : [{ role: "model", text: persona.first_message }],
    input: "",
    loading: false,
  };
}

function createChatSessions() {
  return Object.fromEntries(
    PERSONAS.map((persona) => [persona.id, createChatSession(persona)]),
  );
}

export function PersistentPage({ active, children }) {
  return (
    <div hidden={!active} aria-hidden={!active}>
      {children}
    </div>
  );
}

/** 角色头像：优先立绘图，缺图回退 emoji */
function Avatar({ persona, size = 40 }) {
  const [broken, setBroken] = useState(false);
  return (
    <div
      className="avatar"
      style={{
        width: size,
        height: size,
        "--pc": persona.accent,
      }}
    >
      {!broken ? (
        <img
          src={persona.img}
          alt={persona.name}
          onError={() => setBroken(true)}
        />
      ) : (
        <span style={{ fontSize: size * 0.5 }}>{persona.emoji}</span>
      )}
    </div>
  );
}

// ──────────────────────────────────────────
// 顶部导航
// ──────────────────────────────────────────

function Nav({ page, setPage }) {
  const items = [
    { key: "home", label: "首页", en: "HOME" },
    { key: "personas", label: "角色", en: "CAST" },
    { key: "chat", label: "对话", en: "CHAT" },
    { key: "plan", label: "计划", en: "PLAN" },
  ];
  return (
    <nav className="nav">
      <div className="nav-logo" onClick={() => setPage("home")}>
        <span className="nav-mark">上岸</span>
        <span className="nav-sub">SHORE — 学习陪伴助手</span>
      </div>
      <div className="nav-links">
        {items.map((it) => (
          <button
            key={it.key}
            className={`nav-link ${page === it.key ? "active" : ""}`}
            onClick={() => setPage(it.key)}
          >
            <span className="nav-link-en">{it.en}</span>
            {it.label}
          </button>
        ))}
      </div>
    </nav>
  );
}

// ──────────────────────────────────────────
// 跑马灯
// ──────────────────────────────────────────

function Marquee() {
  const words =
    "上岸 SHORE ✦ 打卡 CHECK-IN ✦ 遗忘曲线 EBBINGHAUS ✦ 推词 VOCAB ✦ 陪伴 COMPANION ✦ 倒计时 COUNTDOWN ✦ ";
  return (
    <div className="marquee" aria-hidden>
      <div className="marquee-track">
        <span>{words.repeat(3)}</span>
        <span>{words.repeat(3)}</span>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// 首页
// ──────────────────────────────────────────

function Home({ setPage, setChatPersona }) {
  return (
    <div className="page">
      <section className="hero">
        <div className="hero-tag">PRIVATE STUDY COMPANION / 专注学习与计划管理</div>
        <h1 className="hero-title">
          <span className="stroke">一起</span>
          <span className="solid">上岸</span>
          <span className="hero-jp">ショア</span>
        </h1>
        <p className="hero-sub">
          打卡 × 计划 × 推词 × 陪聊 —— 学习路上，永远有人陪。
        </p>
        <div className="hero-actions">
          <button className="btn-solid" onClick={() => setPage("chat")}>
            立即对话 →
          </button>
          <button className="btn-line" onClick={() => setPage("personas")}>
            认识她们
          </button>
        </div>
        <div className="hero-meta">
          <span>07:30 早安推送</span>
          <span className="dot" />
          <span>22:30 晚间复盘</span>
          <span className="dot" />
          <span>NoneBot2 / OneBot v11</span>
        </div>
      </section>

      <Marquee />

      <section className="section">
        <div className="section-head">
          <span className="section-no">01</span>
          <h2>功能 / FEATURES</h2>
        </div>
        <div className="feature-grid">
          {FEATURES.map((f, i) => (
            <div className="feature-card" key={f.title}>
              <div className="feature-idx">{String(i + 1).padStart(2, "0")}</div>
              <div className="feature-emoji">{f.emoji}</div>
              <div className="feature-name">{f.title}</div>
              <div className="feature-desc">{f.desc}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <span className="section-no">02</span>
          <h2>角色 / CAST</h2>
        </div>
        <div className="cast-strip">
          {PERSONAS.map((p) => (
            <button
              key={p.id}
              className="cast-chip"
              style={{ "--pc": p.accent }}
              onClick={() => {
                setChatPersona(p.id);
                setPage("chat");
              }}
            >
              <Avatar persona={p} size={44} />
              <div className="cast-chip-text">
                <b>{p.name}</b>
                <i>{p.archetype}</i>
              </div>
              <span className="cast-chip-arrow">→</span>
            </button>
          ))}
        </div>
      </section>

      <footer className="footer">
        上岸 SHORE © 2026 — Python 3.10 · NoneBot2 · aiosqlite · APScheduler · MIT
      </footer>
    </div>
  );
}

// ──────────────────────────────────────────
// 角色展示页
// ──────────────────────────────────────────

function PersonaCard({ p, onChat }) {
  const [broken, setBroken] = useState(false);
  return (
    <div className="persona-card" style={{ "--pc": p.accent, "--pc2": p.accent2 }}>
      <div className="persona-art">
        {!broken ? (
          <img src={p.img} alt={p.name} onError={() => setBroken(true)} />
        ) : (
          <div className="persona-art-fallback">{p.emoji}</div>
        )}
        <div className="persona-art-shade" />
        <span className="persona-en">{p.en}</span>
      </div>
      <div className="persona-body">
        <div className="persona-title-row">
          <span className="persona-name">{p.name}</span>
          <span className="persona-archetype">{p.archetype}</span>
        </div>
        <p className="persona-tagline">「{p.tagline}」</p>
        <div className="persona-traits">
          {p.traits.map((t) => (
            <span className="trait-chip" key={t}>{t}</span>
          ))}
        </div>
        <button className="btn-solid full" onClick={onChat}>
          和{p.name}对话 →
        </button>
      </div>
    </div>
  );
}

function Personas({ setPage, setChatPersona }) {
  return (
    <div className="page">
      <div className="section-head big">
        <span className="section-no">CAST</span>
        <h2>四位陪伴伙伴</h2>
        <p>性格迥异的她们，陪你走过认真学习的每一天</p>
      </div>
      <div className="persona-grid">
        {PERSONAS.map((p) => (
          <PersonaCard
            key={p.id}
            p={p}
            onChat={() => {
              setChatPersona(p.id);
              setPage("chat");
            }}
          />
        ))}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// 在线对话页
// ──────────────────────────────────────────

function Chat({
  chatPersona,
  setChatPersona,
  session,
  updateSession,
}) {
  const persona = PERSONAS.find((p) => p.id === chatPersona) || PERSONAS[0];
  const { messages, input, loading } = session;
  const [backendOk, setBackendOk] = useState(null);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    fetch("/api/personas")
      .then((r) => setBackendOk(r.ok))
      .catch(() => setBackendOk(false));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    updateSession((current) => ({
      ...current,
      input: "",
      loading: true,
      messages: [...current.messages, { role: "user", text }],
    }));
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: getChatSessionId(persona.id),
          persona_id: persona.id,
          message: text,
        }),
      });
      const data = await res.json();
      const reply = data.ok ? data.reply : data.error || "出错了，请稍后再试…";
      updateSession((current) => ({
        ...current,
        messages: [...current.messages, { role: "model", text: reply }],
      }));
    } catch {
      updateSession((current) => ({
        ...current,
        messages: [
          ...current.messages,
          { role: "model", text: "（连不上服务后端…请确认已运行 python main.py）" },
        ],
      }));
    } finally {
      updateSession((current) => ({ ...current, loading: false }));
    }
  }

  function useCommand(command) {
    const template = command.replace(/\s*<[^>]+>/g, " ");
    updateSession((current) => ({ ...current, input: template }));
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  return (
    <div className="page chat-page" style={{ "--pc": persona.accent }}>
      <div className="chat-persona-bar">
        {PERSONAS.map((p) => (
          <button
            key={p.id}
            className={`persona-tab ${p.id === persona.id ? "active" : ""}`}
            style={{ "--pc": p.accent }}
            onClick={() => setChatPersona(p.id)}
          >
            <Avatar persona={p} size={30} />
            {p.name}
          </button>
        ))}
      </div>

      {backendOk === false && (
        <div className="backend-warn">
          ⚠ 未检测到服务后端（127.0.0.1:8080）。请先启动 <code>python main.py</code>。
        </div>
      )}

      <div className="chat-command-bar" aria-label="快捷指令">
        {COMMANDS.map((c) => (
          <button
            key={c.cmd}
            className="command-pill"
            type="button"
            title={c.desc}
            onClick={() => useCommand(c.cmd)}
          >
            <span className="command-pill-code">{c.cmd}</span>
            <span className="command-pill-desc">{c.desc}</span>
          </button>
        ))}
      </div>

      <div className="chat-window">
        <div className="chat-watermark">{persona.en}</div>
        {messages.map((m, i) => (
          <div key={i} className={`msg-row ${m.role === "user" ? "mine" : ""}`}>
            {m.role === "model" && <Avatar persona={persona} size={40} />}
            <div className={`bubble ${m.role === "user" ? "bubble-user" : "bubble-bot"}`}>
              {m.text}
            </div>
            {m.role === "user" && <div className="avatar user-avatar">🎒</div>}
          </div>
        ))}
        {loading && (
          <div className="msg-row">
            <Avatar persona={persona} size={40} />
            <div className="bubble bubble-bot typing">
              <span /><span /><span />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-bar">
        <input
          ref={inputRef}
          className="chat-input"
          value={input}
          placeholder={`和${persona.name}说点什么…（回车发送）`}
          onChange={(e) =>
            updateSession((current) => ({
              ...current,
              input: e.target.value,
            }))
          }
          onKeyDown={(e) => e.key === "Enter" && send()}
          maxLength={500}
        />
        <button className="btn-solid send-btn" onClick={send} disabled={loading}>
          发送
        </button>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// 计划页
// ──────────────────────────────────────────

function toISODate(date) {
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

function getWeekStart() {
  const now = new Date();
  const day = now.getDay() || 7;
  now.setDate(now.getDate() - day + 1);
  return toISODate(now);
}

function createPlanRow(overrides = {}) {
  return {
    id: `draft-${crypto.randomUUID()}`,
    plan_date: toISODate(new Date()),
    scheduled_time: "08:30",
    subject_name: "英语",
    topic_name: "",
    estimated_minutes: 45,
    status: "pending",
    notes: "",
    ...overrides,
  };
}

function Plan() {
  const [weekStart, setWeekStart] = useState(getWeekStart());
  const [items, setItems] = useState([createPlanRow()]);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [goal, setGoal] = useState("");
  const [statusText, setStatusText] = useState("正在读取计划…");

  const filledItems = items.filter((item) => item.topic_name.trim());
  const doneCount = filledItems.filter((item) => item.status === "done").length;
  const totalMinutes = filledItems.reduce(
    (sum, item) => sum + Number(item.estimated_minutes || 0),
    0,
  );

  useEffect(() => {
    fetch(`/api/plan?session_id=${encodeURIComponent(getSessionId())}`)
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) {
          setStatusText(data.error || "计划读取失败");
          return;
        }
        if (data.week_start) setWeekStart(data.week_start);
        setItems(data.items?.length ? data.items : [createPlanRow()]);
        setStatusText(data.items?.length ? "计划已同步" : "暂无计划");
      })
      .catch(() => setStatusText("未连接服务后端，计划暂不能保存"));
  }, []);

  function addPlanRow() {
    setItems((current) => [...current, createPlanRow()]);
    setStatusText("已新增一行");
  }

  function updateItem(targetId, patch) {
    setItems((current) =>
      current.map((item) =>
        item.id === targetId ? { ...item, ...patch } : item,
      ),
    );
  }

  function removeItem(targetId) {
    setItems((current) => {
      const next = current.filter((item) => item.id !== targetId);
      return next.length ? next : [createPlanRow()];
    });
    setStatusText("已移除，保存后生效");
  }

  async function generatePlan() {
    const text = goal.trim();
    if (!text) {
      setStatusText("先告诉 AI 你想学什么");
      return;
    }
    setGenerating(true);
    setStatusText("AI 正在生成计划…");
    try {
      const res = await fetch("/api/plan/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: getSessionId(),
          goal: text,
          week_start: weekStart,
          daily_minutes: 120,
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        setStatusText(data.error || "AI 生成失败");
        return;
      }
      setWeekStart(data.week_start);
      setItems(data.items?.length ? data.items : [createPlanRow()]);
      setStatusText("AI 计划已生成，可继续微调");
    } catch {
      setStatusText("AI 生成失败，请确认服务后端已启动");
    } finally {
      setGenerating(false);
    }
  }

  async function savePlan() {
    if (!filledItems.length) {
      setStatusText("先填一条计划再保存");
      return;
    }
    setSaving(true);
    setStatusText("正在保存…");
    try {
      const res = await fetch("/api/plan", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: getSessionId(),
          week_start: weekStart,
          items: filledItems.map((item) => ({
            plan_date: item.plan_date,
            scheduled_time: item.scheduled_time,
            subject_name: item.subject_name,
            topic_name: item.topic_name.trim(),
            estimated_minutes: Number(item.estimated_minutes || 45),
            status: item.status || "pending",
            notes: item.notes || "",
          })),
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        setStatusText(data.error || "保存失败");
        return;
      }
      setWeekStart(data.week_start);
      setItems(data.items?.length ? data.items : [createPlanRow()]);
      setStatusText("计划已保存");
    } catch {
      setStatusText("保存失败，请确认服务后端已启动");
    } finally {
      setSaving(false);
    }
  }

  async function toggleStatus(item) {
    const nextStatus = item.status === "done" ? "pending" : "done";
    updateItem(item.id, { status: nextStatus });
    if (String(item.id).startsWith("draft-")) {
      setStatusText("已更新，保存后生效");
      return;
    }

    try {
      const res = await fetch("/api/plan/status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: getSessionId(),
          plan_id: item.id,
          status: nextStatus,
        }),
      });
      const data = await res.json();
      setStatusText(data.ok ? "状态已同步" : data.error || "状态同步失败");
    } catch {
      setStatusText("状态同步失败，请确认服务后端已启动");
    }
  }

  return (
    <div className="page plan-page">
      <div className="section-head big">
        <span className="section-no">PLAN</span>
        <h2>学习计划</h2>
        <p>{statusText}</p>
      </div>

      <section className="plan-sheet">
        <div className="plan-ai-bar">
          <input
            value={goal}
            maxLength={500}
            placeholder="输入你想学的内容，例如：两周入门 Python 数据分析、复习线性代数矩阵、每天练英语听力"
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && generatePlan()}
          />
          <button
            className="btn-solid"
            type="button"
            onClick={generatePlan}
            disabled={generating}
          >
            AI 生成计划
          </button>
        </div>

        <div className="plan-toolbar">
          <div className="plan-toolbar-meta">
            <label>
              <span>周起始</span>
              <input
                type="date"
                value={weekStart}
                onChange={(e) => setWeekStart(e.target.value)}
              />
            </label>
            <span>{doneCount} / {filledItems.length} 已完成</span>
            <span>{totalMinutes} min</span>
          </div>
          <div className="plan-toolbar-actions">
            <button className="btn-line compact" type="button" onClick={addPlanRow}>
              新增一行
            </button>
            <button
              className="btn-solid"
              type="button"
              onClick={savePlan}
              disabled={saving}
            >
              保存计划
            </button>
          </div>
        </div>

        <div className="plan-table-wrap">
          <div className="plan-table">
            <div className="plan-table-head">
              <span>日期</span>
              <span>时间</span>
              <span>科目</span>
              <span>计划内容</span>
              <span>分钟</span>
              <span>状态</span>
              <span />
            </div>
            {items.map((item) => (
              <div className={`plan-table-row ${item.status}`} key={item.id}>
                <input
                  type="date"
                  value={item.plan_date}
                  onChange={(e) => updateItem(item.id, { plan_date: e.target.value })}
                />
                <input
                  type="time"
                  value={item.scheduled_time}
                  onChange={(e) =>
                    updateItem(item.id, { scheduled_time: e.target.value })
                  }
                />
                <input
                  value={item.subject_name}
                  maxLength={50}
                  placeholder="科目"
                  onChange={(e) =>
                    updateItem(item.id, { subject_name: e.target.value })
                  }
                />
                <input
                  value={item.topic_name}
                  maxLength={120}
                  placeholder="直接填写计划内容"
                  onChange={(e) => updateItem(item.id, { topic_name: e.target.value })}
                />
                <input
                  type="number"
                  min="1"
                  max="600"
                  value={item.estimated_minutes}
                  onChange={(e) =>
                    updateItem(item.id, { estimated_minutes: e.target.value })
                  }
                />
                <button
                  className="plan-status-cell"
                  type="button"
                  onClick={() => toggleStatus(item)}
                >
                  {item.status === "done" ? "完成" : "待办"}
                </button>
                <button
                  className="plan-remove-cell"
                  type="button"
                  onClick={() => removeItem(item.id)}
                  aria-label="删除计划"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

// ──────────────────────────────────────────
// App
// ──────────────────────────────────────────

export default function App() {
  const [page, setPage] = useState("home");
  const [visitedPages, setVisitedPages] = useState(() => new Set(["home"]));
  const [chatPersona, setChatPersona] = useState("kitty");
  const [chatSessions, setChatSessions] = useState(createChatSessions);

  useEffect(() => {
    for (const [personaId, session] of Object.entries(chatSessions)) {
      sessionStorage.setItem(
        chatStorageKey(personaId),
        JSON.stringify(session.messages.slice(-40)),
      );
    }
  }, [chatSessions]);

  function updateChatSession(personaId, updater) {
    setChatSessions((current) => {
      const previous =
        current[personaId] ||
        createChatSession(
          PERSONAS.find((persona) => persona.id === personaId) || PERSONAS[0],
        );
      return {
        ...current,
        [personaId]: updater(previous),
      };
    });
  }

  function navigate(nextPage) {
    setVisitedPages((current) => {
      if (current.has(nextPage)) return current;
      const next = new Set(current);
      next.add(nextPage);
      return next;
    });
    setPage(nextPage);
  }

  return (
    <div className="app">
      <div className="bg-grid" aria-hidden />
      <div className="bg-noise" aria-hidden />
      <Nav page={page} setPage={navigate} />
      <PersistentPage active={page === "home"}>
        <Home setPage={navigate} setChatPersona={setChatPersona} />
      </PersistentPage>
      {visitedPages.has("personas") && (
        <PersistentPage active={page === "personas"}>
          <Personas setPage={navigate} setChatPersona={setChatPersona} />
        </PersistentPage>
      )}
      {visitedPages.has("chat") && (
        <PersistentPage active={page === "chat"}>
          <Chat
            chatPersona={chatPersona}
            setChatPersona={setChatPersona}
            session={chatSessions[chatPersona]}
            updateSession={(updater) =>
              updateChatSession(chatPersona, updater)
            }
          />
        </PersistentPage>
      )}
      {visitedPages.has("plan") && (
        <PersistentPage active={page === "plan"}>
          <Plan />
        </PersistentPage>
      )}
    </div>
  );
}
