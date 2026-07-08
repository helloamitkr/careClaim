"use client";

import { useEffect, useRef, useState } from "react";

const DEFAULT_STREAM_URL = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010"}/api/logs/stream`;
const MAX_LINES = 2000;
const LEVELS = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"] as const;

interface LogRecord {
  time: string;
  level: string;
  component: string;
  message: string;
  extra: Record<string, unknown>;
}

const LEVEL_STYLES: Record<string, string> = {
  DEBUG: "text-zinc-500",
  INFO: "text-teal-400",
  WARNING: "text-amber-400",
  ERROR: "text-red-400",
  CRITICAL: "text-red-300 bg-red-950",
};

function LogLine({ record }: { record: LogRecord }) {
  if (record.level === "PING") {
    return (
      <div className="leading-5 text-zinc-700 italic">
        <span className="text-zinc-700">{record.time.slice(11)}</span> · ping — stream
        alive, no new logs
      </div>
    );
  }

  const agentId = record.extra.agent_id as string | undefined;
  const extras = Object.entries(record.extra).filter(
    ([k, v]) => v !== null && k !== "agent_id",
  );
  return (
    <div className="whitespace-pre-wrap break-all leading-5 hover:bg-white/[0.04]">
      <span className="text-zinc-600">{record.time.slice(11)}</span>{" "}
      <span className={`font-semibold ${LEVEL_STYLES[record.level] ?? "text-zinc-300"}`}>
        {record.level.padEnd(7)}
      </span>{" "}
      <span className="text-sky-400">{record.component}</span>
      {agentId && (
        <>
          {" "}
          <span className="rounded bg-violet-950 px-1.5 py-px text-violet-300 border border-violet-800">
            {agentId}
          </span>
        </>
      )}{" "}
      <span className="text-zinc-200">{record.message}</span>
      {extras.length > 0 && (
        <span className="text-zinc-600">
          {"  "}
          {extras.map(([k, v]) => `${k}=${String(v)}`).join(" ")}
        </span>
      )}
    </div>
  );
}

export default function LogsPage() {
  const [url, setUrl] = useState(DEFAULT_STREAM_URL);
  const [running, setRunning] = useState(false);
  const [records, setRecords] = useState<LogRecord[]>([]);
  const [levelFilter, setLevelFilter] = useState<(typeof LEVELS)[number]>("ALL");
  const [autoScroll, setAutoScroll] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastPing, setLastPing] = useState<string | null>(null);

  const sourceRef = useRef<EventSource | null>(null);
  const paneRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    return () => sourceRef.current?.close(); // leaving the page closes the stream
  }, []);

  useEffect(() => {
    if (autoScroll && paneRef.current) {
      paneRef.current.scrollTop = paneRef.current.scrollHeight;
    }
  }, [records, autoScroll]);

  function append(newRecords: LogRecord[]) {
    setRecords((prev) => [...prev, ...newRecords].slice(-MAX_LINES));
  }

  async function start() {
    setError(null);

    // Backfill recent history first when the URL looks like our stream
    // endpoint — the page isn't blank while waiting for the next event.
    if (url.endsWith("/api/logs/stream")) {
      try {
        const response = await fetch(url.replace(/\/stream$/, "/tail?lines=100"));
        if (response.ok) append((await response.json()) as LogRecord[]);
      } catch {
        // no backfill — the live stream may still connect fine
      }
    }

    const source = new EventSource(url);
    sourceRef.current = source;

    source.onopen = () => setRunning(true);
    source.onmessage = (event) => {
      try {
        append([JSON.parse(event.data) as LogRecord]);
      } catch {
        append([
          { time: "", level: "INFO", component: "raw", message: event.data, extra: {} },
        ]);
      }
    };
    source.addEventListener("ping", () => {
      const now = new Date();
      setLastPing(now.toLocaleTimeString());
      append([
        {
          time: `1970-01-01 ${now.toTimeString().slice(0, 8)}`,
          level: "PING",
          component: "stream",
          message: "ping",
          extra: {},
        },
      ]);
    });
    source.onerror = () => {
      setError(`Connection to ${url} failed or was lost.`);
      source.close();
      sourceRef.current = null;
      setRunning(false);
    };
  }

  function stop() {
    sourceRef.current?.close();
    sourceRef.current = null;
    setRunning(false);
  }

  const visible =
    levelFilter === "ALL" ? records : records.filter((r) => r.level === levelFilter);

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 space-y-6">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Live logs</h1>
        <p className="text-sm text-black/50 dark:text-white/50">
          Point this at a CareBridge log stream URL and watch the pipeline think in real
          time — every event, agent decision, guardrail action, and API request.
        </p>
      </section>

      <section className="flex flex-wrap items-center gap-3">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={running}
          spellCheck={false}
          placeholder="http://localhost:8010/api/logs/stream"
          className="flex-1 min-w-72 rounded-lg border border-black/10 dark:border-white/10 bg-transparent px-3 py-2 font-mono text-xs focus:outline-none focus:border-teal-500 disabled:opacity-50"
        />
        <button
          onClick={running ? stop : start}
          className={`rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors ${
            running ? "bg-red-600 hover:bg-red-500" : "bg-teal-600 hover:bg-teal-500"
          }`}
        >
          {running ? "Stop" : "Start"}
        </button>
        <button
          onClick={() => setRecords([])}
          className="rounded-lg border border-black/10 dark:border-white/10 px-4 py-2 text-sm hover:border-teal-500 transition-colors"
        >
          Clear
        </button>
        <select
          value={levelFilter}
          onChange={(e) => setLevelFilter(e.target.value as (typeof LEVELS)[number])}
          className="rounded-lg border border-black/10 dark:border-white/10 bg-transparent px-3 py-2 text-sm focus:outline-none focus:border-teal-500"
        >
          {LEVELS.map((level) => (
            <option key={level} value={level}>
              {level === "ALL" ? "All levels" : level}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-black/60 dark:text-white/60 cursor-pointer">
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(e) => setAutoScroll(e.target.checked)}
            className="accent-teal-600"
          />
          Follow
        </label>
      </section>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <section>
        <div
          ref={paneRef}
          className="h-[65vh] overflow-y-auto rounded-lg border border-black/10 dark:border-white/10 bg-zinc-950 p-4 font-mono text-xs"
        >
          {visible.length === 0 ? (
            <p className="text-zinc-600">
              {running
                ? "Connected — waiting for log events…"
                : "Not connected. Press Start to begin streaming."}
            </p>
          ) : (
            visible.map((record, i) => <LogLine key={i} record={record} />)
          )}
        </div>
        <p className="mt-2 text-xs text-black/40 dark:text-white/40">
          {running ? "● streaming" : "○ stopped"} · {visible.length} line
          {visible.length === 1 ? "" : "s"} shown
          {levelFilter !== "ALL" ? ` (${records.length} total)` : ""} · keeps the last{" "}
          {MAX_LINES} lines
          {lastPing && ` · last ping ${lastPing}`}
        </p>
      </section>
    </div>
  );
}
