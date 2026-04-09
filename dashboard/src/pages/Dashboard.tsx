import { useCallback, useEffect, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  Distribution,
  GoalStats,
  GrowthPoint,
  Hashtag,
  Influencer,
  QueueItem,
  RunLog,
  Stats,
} from "../api";

// ─────────────────────────────────────────────
// 상수
// ─────────────────────────────────────────────
const REFRESH_INTERVAL = 30_000; // 30초

const TIER_COLOR: Record<string, string> = {
  nano: "#818cf8",
  micro: "#34d399",
  mid: "#fbbf24",
  macro: "#f87171",
};

const JOB_LABEL: Record<string, string> = {
  discovery: "Discovery",
  enrichment: "Enrichment",
  profile_refresh: "Refresh",
  post_refresh: "Post Refresh",
};

const DOMAIN_LABEL: Record<string, string> = {
  skin_clinic: "피부과",
  plastic_surgery: "성형외과",
  obesity_clinic: "비만클리닉",
};

// ─────────────────────────────────────────────
// 헬퍼
// ─────────────────────────────────────────────
function fmt(n: number | null | undefined) {
  if (n == null) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  return n.toLocaleString();
}

function fmtDuration(sec: number | null) {
  if (sec == null) return "-";
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

function timeAgo(iso: string | null) {
  if (!iso) return "-";
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}초 전`;
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
  return `${Math.floor(diff / 86400)}일 전`;
}

// ─────────────────────────────────────────────
// 컴포넌트: 통계 카드
// ─────────────────────────────────────────────
function StatCard({
  label,
  value,
  sub,
  color = "indigo",
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: "indigo" | "green" | "yellow" | "red" | "slate";
}) {
  const ring: Record<string, string> = {
    indigo: "border-indigo-500/40",
    green: "border-emerald-500/40",
    yellow: "border-yellow-500/40",
    red: "border-red-500/40",
    slate: "border-slate-600/40",
  };
  const text: Record<string, string> = {
    indigo: "text-indigo-400",
    green: "text-emerald-400",
    yellow: "text-yellow-400",
    red: "text-red-400",
    slate: "text-slate-400",
  };
  return (
    <div className={`bg-[#161b27] border ${ring[color]} rounded-xl p-5`}>
      <p className="text-xs text-slate-500 uppercase tracking-widest mb-1">{label}</p>
      <p className={`text-3xl font-bold ${text[color]}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  );
}

// ─────────────────────────────────────────────
// 컴포넌트: 섹션 헤더
// ─────────────────────────────────────────────
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-[#161b27] border border-slate-700/40 rounded-xl p-6">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-widest mb-4">
        {title}
      </h2>
      {children}
    </div>
  );
}

// ─────────────────────────────────────────────
// 컴포넌트: 배지
// ─────────────────────────────────────────────
function Badge({ text, color }: { text: string; color?: string }) {
  const base = "text-[10px] font-medium px-2 py-0.5 rounded-full";
  const cls =
    color === "green"
      ? "bg-emerald-900/50 text-emerald-400"
      : color === "red"
      ? "bg-red-900/50 text-red-400"
      : color === "yellow"
      ? "bg-yellow-900/50 text-yellow-400"
      : color === "indigo"
      ? "bg-indigo-900/50 text-indigo-400"
      : "bg-slate-700/50 text-slate-400";
  return <span className={`${base} ${cls}`}>{text}</span>;
}

function tierBadge(tier: string | null) {
  if (!tier) return null;
  const c = tier === "nano" ? "indigo" : tier === "micro" ? "green" : tier === "mid" ? "yellow" : "red";
  return <Badge text={tier} color={c} />;
}

function jobBadge(jobType: string) {
  const c =
    jobType === "discovery"
      ? "indigo"
      : jobType === "enrichment"
      ? "green"
      : jobType === "profile_refresh"
      ? "yellow"
      : "slate";
  return <Badge text={JOB_LABEL[jobType] ?? jobType} color={c} />;
}

// ─────────────────────────────────────────────
// 메인 대시보드
// ─────────────────────────────────────────────
export default function Dashboard() {
  const [goal, setGoal] = useState<GoalStats | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [distribution, setDistribution] = useState<Distribution | null>(null);
  const [growth, setGrowth] = useState<GrowthPoint[]>([]);
  const [runs, setRuns] = useState<RunLog[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [hashtags, setHashtags] = useState<Hashtag[]>([]);
  const [influencers, setInfluencers] = useState<Influencer[]>([]);
  const [infTotal, setInfTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [jobRunning, setJobRunning] = useState<string | null>(null);

  // 필터
  const [tierFilter, setTierFilter] = useState("");
  const [domainFilter, setDomainFilter] = useState("skin_clinic");
  const [growthDays, setGrowthDays] = useState(30);
  const [infPage, setInfPage] = useState(1);

  const fetchAll = useCallback(async () => {
    try {
      const [gl, s, d, g, r, q, h, inf] = await Promise.all([
        api.getGoal(),
        api.getStats(),
        api.getDistribution(),
        api.getGrowth(growthDays),
        api.getRuns(),
        api.getQueue(),
        api.getHashtags(),
        api.getInfluencers(infPage, tierFilter || undefined, domainFilter || undefined),
      ]);
      setGoal(gl);
      setStats(s);
      setDistribution(d);
      setGrowth(g);
      setRuns(r);
      setQueue(q);
      setHashtags(h);
      setInfluencers(inf.items);
      setInfTotal(inf.total);
      setLastUpdated(new Date());
    } catch (e) {
      console.error("API fetch error:", e);
    } finally {
      setLoading(false);
    }
  }, [growthDays, tierFilter, domainFilter, infPage]);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, REFRESH_INTERVAL);
    return () => clearInterval(id);
  }, [fetchAll]);

  const triggerJob = async (type: string) => {
    setJobRunning(type);
    try {
      if (type === "discovery") await api.triggerDiscovery();
      else if (type === "enrichment") await api.triggerEnrichment();
      else await api.triggerRefresh(type);
      await fetchAll();
    } finally {
      setJobRunning(null);
    }
  };

  // ── 큐 집계
  const queueByStatus = queue.reduce<Record<string, number>>((acc, q) => {
    acc[q.status] = (acc[q.status] || 0) + q.count;
    return acc;
  }, {});

  // ── 도메인 바 데이터
  const domainBarData = distribution
    ? [
        { name: "피부과", value: distribution.domain_scores.skin_clinic, fill: "#818cf8" },
        { name: "성형외과", value: distribution.domain_scores.plastic_surgery, fill: "#34d399" },
        { name: "비만클리닉", value: distribution.domain_scores.obesity_clinic, fill: "#fbbf24" },
      ]
    : [];

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-slate-400 text-sm animate-pulse">데이터 로딩 중...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200 p-6 space-y-6">
      {/* ── 헤더 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">
            Feefluencer · 시딩 DB 모니터
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            마지막 갱신: {lastUpdated ? lastUpdated.toLocaleTimeString("ko-KR") : "-"} &nbsp;·&nbsp;
            {REFRESH_INTERVAL / 1000}초마다 자동 새로고침
          </p>
        </div>
        {/* 수동 배치 트리거 */}
        <div className="flex gap-2">
          {["discovery", "enrichment", "hot", "warm"].map((job) => (
            <button
              key={job}
              onClick={() => triggerJob(job)}
              disabled={!!jobRunning}
              className="text-xs px-3 py-1.5 rounded-lg border border-slate-600 text-slate-300
                         hover:border-indigo-500 hover:text-indigo-400 disabled:opacity-40
                         transition-colors"
            >
              {jobRunning === job ? "실행 중..." : `▶ ${JOB_LABEL[job] ?? job.toUpperCase()}`}
            </button>
          ))}
        </div>
      </div>

      {/* ── 1차 목표 진행률 */}
      {goal && (
        <div className="bg-[#161b27] border border-indigo-500/30 rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <div>
              <span className="text-sm font-semibold text-white">1차 목표 — 의미있는 인플루언서 5,000명</span>
              <span className="ml-3 text-xs text-slate-500">
                활동 중 · Enrichment 완료 · match_score &gt; 0
              </span>
            </div>
            <span className="text-2xl font-bold text-indigo-400">
              {goal.meaningful.toLocaleString()} <span className="text-sm text-slate-500">/ {goal.goal.toLocaleString()}</span>
            </span>
          </div>

          {/* 메인 프로그레스 바 */}
          <div className="w-full h-3 bg-slate-800 rounded-full overflow-hidden mb-4">
            <div
              className="h-full bg-gradient-to-r from-indigo-600 to-indigo-400 rounded-full transition-all duration-700"
              style={{ width: `${Math.min(goal.progress_pct, 100)}%` }}
            />
          </div>

          {/* 파이프라인 단계별 현황 */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-center">
            <div className="bg-slate-800/40 rounded-lg py-2.5">
              <p className="text-[10px] text-slate-500 mb-0.5">전체 수집</p>
              <p className="text-base font-bold text-slate-300">{fmt(goal.total_collected)}</p>
            </div>
            <div className="bg-slate-800/40 rounded-lg py-2.5">
              <p className="text-[10px] text-slate-500 mb-0.5">Enrichment 완료</p>
              <p className="text-base font-bold text-yellow-400">{fmt(goal.enriched)}</p>
            </div>
            <div className="bg-slate-800/40 rounded-lg py-2.5">
              <p className="text-[10px] text-slate-500 mb-0.5">Enrichment 대기</p>
              <p className="text-base font-bold text-blue-400">{fmt(goal.pending_enrichment)}</p>
            </div>
            <div className="bg-slate-800/40 rounded-lg py-2.5">
              <p className="text-[10px] text-slate-500 mb-0.5">의미있는 계정</p>
              <p className="text-base font-bold text-indigo-400">{fmt(goal.meaningful)}</p>
            </div>
            <div className="bg-slate-800/40 rounded-lg py-2.5">
              <p className="text-[10px] text-slate-500 mb-0.5">제외 (업체/저품질)</p>
              <p className="text-base font-bold text-slate-600">{fmt(goal.discarded)}</p>
            </div>
          </div>

          {/* 도메인별 breakdown */}
          <div className="flex gap-4 mt-3 pt-3 border-t border-slate-700/30">
            <span className="text-[10px] text-slate-500 self-center">score &gt; 0.3</span>
            <span className="text-xs text-indigo-300">
              피부과 <strong>{fmt(goal.domain_breakdown.skin_clinic)}</strong>
            </span>
            <span className="text-xs text-emerald-300">
              성형외과 <strong>{fmt(goal.domain_breakdown.plastic_surgery)}</strong>
            </span>
            <span className="text-xs text-yellow-300">
              비만클리닉 <strong>{fmt(goal.domain_breakdown.obesity_clinic)}</strong>
            </span>
            <span className="ml-auto text-xs text-indigo-500 font-semibold">
              {goal.progress_pct}% 달성
            </span>
          </div>
        </div>
      )}

      {/* ── 핵심 지표 카드 */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard
          label="총 인플루언서"
          value={fmt(stats?.total_influencers)}
          color="indigo"
        />
        <StatCard
          label="오늘 신규"
          value={fmt(stats?.new_today)}
          color="green"
        />
        <StatCard
          label="큐 대기"
          value={fmt(stats?.queue_pending)}
          sub={`실행 중 ${stats?.queue_running ?? 0}개`}
          color="yellow"
        />
        <StatCard
          label="이상 감지"
          value={fmt(stats?.anomaly_count)}
          sub="팔로워 이상 변동"
          color="red"
        />
        <StatCard
          label="최근 배치"
          value={timeAgo(stats?.last_run.started_at ?? null)}
          sub={stats?.last_run.job_type ? JOB_LABEL[stats.last_run.job_type] ?? stats.last_run.job_type : "-"}
          color="slate"
        />
      </div>

      {/* ── 성장 차트 + 티어 분포 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 성장 차트 */}
        <div className="lg:col-span-2">
          <Section title="누적 인플루언서 성장">
            <div className="flex gap-2 mb-4">
              {[7, 14, 30, 60].map((d) => (
                <button
                  key={d}
                  onClick={() => setGrowthDays(d)}
                  className={`text-xs px-2.5 py-1 rounded-md transition-colors ${
                    growthDays === d
                      ? "bg-indigo-600 text-white"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                >
                  {d}일
                </button>
              ))}
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={growth} margin={{ top: 4, right: 4, left: -10, bottom: 0 }}>
                <defs>
                  <linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2533" />
                <XAxis
                  dataKey="date"
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickFormatter={(v) => v.slice(5)}
                />
                <YAxis tick={{ fill: "#64748b", fontSize: 11 }} tickFormatter={fmt} />
                <Tooltip
                  contentStyle={{ background: "#161b27", border: "1px solid #334155", borderRadius: 8 }}
                  labelStyle={{ color: "#94a3b8" }}
                  itemStyle={{ color: "#a5b4fc" }}
                  formatter={(v: number) => [fmt(v)]}
                />
                <Area
                  type="monotone"
                  dataKey="cumulative"
                  name="누적"
                  stroke="#6366f1"
                  strokeWidth={2}
                  fill="url(#grad)"
                />
                <Area
                  type="monotone"
                  dataKey="new_count"
                  name="신규"
                  stroke="#34d399"
                  strokeWidth={1.5}
                  fill="none"
                  strokeDasharray="4 2"
                />
              </AreaChart>
            </ResponsiveContainer>
          </Section>
        </div>

        {/* 티어 분포 */}
        <Section title="팔로워 티어 분포">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart
              data={distribution?.tiers ?? []}
              margin={{ top: 4, right: 4, left: -20, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2533" />
              <XAxis dataKey="tier" tick={{ fill: "#64748b", fontSize: 11 }} />
              <YAxis tick={{ fill: "#64748b", fontSize: 11 }} tickFormatter={fmt} />
              <Tooltip
                contentStyle={{ background: "#161b27", border: "1px solid #334155", borderRadius: 8 }}
                itemStyle={{ color: "#a5b4fc" }}
                formatter={(v: number) => [fmt(v)]}
              />
              <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                {(distribution?.tiers ?? []).map((entry) => (
                  <Cell key={entry.tier} fill={TIER_COLOR[entry.tier] ?? "#64748b"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Section>
      </div>

      {/* ── 도메인 스코어 + 큐 현황 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 도메인 스코어 */}
        <Section title="도메인별 매칭 대상 (score ≥ 0.3)">
          <ResponsiveContainer width="100%" height={160}>
            <BarChart
              data={domainBarData}
              layout="vertical"
              margin={{ top: 0, right: 20, left: 20, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2533" horizontal={false} />
              <XAxis type="number" tick={{ fill: "#64748b", fontSize: 11 }} tickFormatter={fmt} />
              <YAxis type="category" dataKey="name" tick={{ fill: "#94a3b8", fontSize: 12 }} width={70} />
              <Tooltip
                contentStyle={{ background: "#161b27", border: "1px solid #334155", borderRadius: 8 }}
                formatter={(v: number) => [fmt(v), "계정"]}
              />
              <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                {domainBarData.map((d, i) => (
                  <Cell key={i} fill={d.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Section>

        {/* 큐 현황 */}
        <Section title="큐 현황">
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(queueByStatus).map(([status, count]) => {
              const color =
                status === "pending"
                  ? "text-yellow-400"
                  : status === "running"
                  ? "text-indigo-400"
                  : status === "done"
                  ? "text-emerald-400"
                  : "text-red-400";
              return (
                <div key={status} className="flex items-center justify-between bg-slate-800/40 rounded-lg px-4 py-3">
                  <span className="text-xs text-slate-400">{status}</span>
                  <span className={`text-lg font-bold ${color}`}>{fmt(count)}</span>
                </div>
              );
            })}
          </div>
          <div className="mt-4 space-y-1 max-h-36 overflow-y-auto">
            {queue.map((q, i) => (
              <div key={i} className="flex items-center justify-between text-xs py-1 border-b border-slate-700/30">
                <div className="flex gap-2 items-center">
                  {jobBadge(q.job_type)}
                  <span className="text-slate-500">{q.status}</span>
                </div>
                <span className="text-slate-300 font-medium">{fmt(q.count)}</span>
              </div>
            ))}
          </div>
        </Section>
      </div>

      {/* ── 배치 실행 로그 */}
      <Section title="최근 배치 실행 이력">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 border-b border-slate-700/40">
                <th className="text-left pb-2 font-medium">작업</th>
                <th className="text-left pb-2 font-medium">시작</th>
                <th className="text-right pb-2 font-medium">소요</th>
                <th className="text-right pb-2 font-medium">시도</th>
                <th className="text-right pb-2 font-medium">성공</th>
                <th className="text-right pb-2 font-medium">실패</th>
                <th className="text-right pb-2 font-medium">신규</th>
                <th className="text-right pb-2 font-medium">Apify 호출</th>
                <th className="text-right pb-2 font-medium">DB 총계</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-b border-slate-800/60 hover:bg-slate-800/20">
                  <td className="py-2">{jobBadge(r.job_type)}</td>
                  <td className="py-2 text-slate-400">{timeAgo(r.started_at)}</td>
                  <td className="py-2 text-right text-slate-400">{fmtDuration(r.duration_seconds)}</td>
                  <td className="py-2 text-right">{fmt(r.total_attempted)}</td>
                  <td className="py-2 text-right text-emerald-400">{fmt(r.success_count)}</td>
                  <td className="py-2 text-right text-red-400">{fmt(r.failed_count)}</td>
                  <td className="py-2 text-right text-indigo-400 font-medium">{fmt(r.new_accounts_found)}</td>
                  <td className="py-2 text-right text-slate-400">{fmt(r.apify_calls_made)}</td>
                  <td className="py-2 text-right text-slate-300">{fmt(r.db_total_after)}</td>
                </tr>
              ))}
              {runs.length === 0 && (
                <tr>
                  <td colSpan={9} className="py-6 text-center text-slate-600">
                    실행 이력 없음
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>

      {/* ── 해시태그 풀 */}
      <Section title="해시태그 풀">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 border-b border-slate-700/40">
                <th className="text-left pb-2 font-medium">해시태그</th>
                <th className="text-left pb-2 font-medium">도메인</th>
                <th className="text-right pb-2 font-medium">수집 횟수</th>
                <th className="text-right pb-2 font-medium">직전 신규</th>
                <th className="text-right pb-2 font-medium">누적 발굴</th>
                <th className="text-left pb-2 font-medium">마지막 수집</th>
                <th className="text-center pb-2 font-medium">상태</th>
              </tr>
            </thead>
            <tbody>
              {hashtags.slice(0, 30).map((h) => (
                <tr key={h.hashtag} className="border-b border-slate-800/60 hover:bg-slate-800/20">
                  <td className="py-1.5 text-indigo-300">#{h.hashtag}</td>
                  <td className="py-1.5 text-slate-400">{DOMAIN_LABEL[h.domain] ?? h.domain}</td>
                  <td className="py-1.5 text-right text-slate-300">{h.crawl_count}</td>
                  <td className="py-1.5 text-right text-emerald-400">{h.new_accounts_found_last}</td>
                  <td className="py-1.5 text-right text-slate-300">{fmt(h.total_accounts_found)}</td>
                  <td className="py-1.5 text-slate-500">{timeAgo(h.last_crawled_at)}</td>
                  <td className="py-1.5 text-center">
                    {h.is_exhausted ? (
                      <Badge text="소진" color="red" />
                    ) : h.last_crawled_at ? (
                      <Badge text="활성" color="green" />
                    ) : (
                      <Badge text="미수집" color="yellow" />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {/* ── 인플루언서 목록 */}
      <Section title="인플루언서 목록">
        {/* 필터 */}
        <div className="flex flex-wrap gap-3 mb-4">
          <select
            value={domainFilter}
            onChange={(e) => { setDomainFilter(e.target.value); setInfPage(1); }}
            className="text-xs bg-slate-800 border border-slate-600 rounded-lg px-3 py-1.5 text-slate-300"
          >
            <option value="skin_clinic">피부과</option>
            <option value="plastic_surgery">성형외과</option>
            <option value="obesity_clinic">비만클리닉</option>
          </select>
          <select
            value={tierFilter}
            onChange={(e) => { setTierFilter(e.target.value); setInfPage(1); }}
            className="text-xs bg-slate-800 border border-slate-600 rounded-lg px-3 py-1.5 text-slate-300"
          >
            <option value="">전체 티어</option>
            <option value="nano">Nano</option>
            <option value="micro">Micro</option>
            <option value="mid">Mid</option>
            <option value="macro">Macro</option>
          </select>
          <span className="text-xs text-slate-500 self-center">총 {fmt(infTotal)}명</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 border-b border-slate-700/40">
                <th className="text-left pb-2 font-medium">핸들</th>
                <th className="text-left pb-2 font-medium">이름</th>
                <th className="text-right pb-2 font-medium">팔로워</th>
                <th className="text-center pb-2 font-medium">티어</th>
                <th className="text-right pb-2 font-medium">ER</th>
                <th className="text-right pb-2 font-medium">피부과</th>
                <th className="text-right pb-2 font-medium">성형</th>
                <th className="text-right pb-2 font-medium">비만</th>
                <th className="text-left pb-2 font-medium">지역</th>
                <th className="text-left pb-2 font-medium">수집일</th>
              </tr>
            </thead>
            <tbody>
              {influencers.map((inf) => (
                <tr
                  key={inf.id}
                  className={`border-b border-slate-800/60 hover:bg-slate-800/20 ${
                    inf.anomaly_flag ? "bg-red-950/10" : ""
                  }`}
                >
                  <td className="py-1.5">
                    <span className="text-indigo-300">@{inf.handle}</span>
                    {inf.anomaly_flag && (
                      <span className="ml-1 text-red-400 text-[10px]">⚠</span>
                    )}
                  </td>
                  <td className="py-1.5 text-slate-400 max-w-[120px] truncate">
                    {inf.full_name ?? "-"}
                  </td>
                  <td className="py-1.5 text-right text-slate-300">{fmt(inf.followers)}</td>
                  <td className="py-1.5 text-center">{tierBadge(inf.follower_tier)}</td>
                  <td className="py-1.5 text-right text-slate-400">
                    {inf.engagement_rate ? `${(inf.engagement_rate * 100).toFixed(2)}%` : "-"}
                  </td>
                  <td className="py-1.5 text-right">
                    <ScoreBar value={inf.match_score_skin_clinic} />
                  </td>
                  <td className="py-1.5 text-right">
                    <ScoreBar value={inf.match_score_plastic_surgery} color="green" />
                  </td>
                  <td className="py-1.5 text-right">
                    <ScoreBar value={inf.match_score_obesity_clinic} color="yellow" />
                  </td>
                  <td className="py-1.5 text-slate-500 max-w-[100px] truncate">
                    {Array.isArray(inf.region_tags) ? inf.region_tags.slice(0, 2).join(", ") : "-"}
                  </td>
                  <td className="py-1.5 text-slate-600">{timeAgo(inf.last_scraped_at)}</td>
                </tr>
              ))}
              {influencers.length === 0 && (
                <tr>
                  <td colSpan={10} className="py-6 text-center text-slate-600">
                    데이터 없음
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* 페이지네이션 */}
        <div className="flex justify-center gap-2 mt-4">
          <button
            onClick={() => setInfPage((p) => Math.max(1, p - 1))}
            disabled={infPage === 1}
            className="text-xs px-3 py-1 rounded-md border border-slate-700 text-slate-400
                       disabled:opacity-30 hover:border-slate-500"
          >
            이전
          </button>
          <span className="text-xs text-slate-500 self-center">
            {infPage} / {Math.ceil(infTotal / 50) || 1}
          </span>
          <button
            onClick={() => setInfPage((p) => p + 1)}
            disabled={infPage * 50 >= infTotal}
            className="text-xs px-3 py-1 rounded-md border border-slate-700 text-slate-400
                       disabled:opacity-30 hover:border-slate-500"
          >
            다음
          </button>
        </div>
      </Section>
    </div>
  );
}

// ─────────────────────────────────────────────
// 스코어 바 컴포넌트
// ─────────────────────────────────────────────
function ScoreBar({
  value,
  color = "indigo",
}: {
  value: number | null;
  color?: "indigo" | "green" | "yellow";
}) {
  if (value == null) return <span className="text-slate-700">-</span>;

  const pct = Math.round(value * 100);
  const bg =
    color === "green"
      ? "bg-emerald-500"
      : color === "yellow"
      ? "bg-yellow-500"
      : "bg-indigo-500";

  return (
    <div className="flex items-center gap-1.5 justify-end">
      <span className="text-slate-300 w-7 text-right">{pct}</span>
      <div className="w-14 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full ${bg} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
