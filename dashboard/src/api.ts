const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function post<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "POST" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export interface Stats {
  total_influencers: number;
  new_today: number;
  queue_pending: number;
  queue_running: number;
  anomaly_count: number;
  last_run: { started_at: string | null; job_type: string | null };
}

export interface Distribution {
  tiers: { tier: string; count: number }[];
  status: { status: string; count: number }[];
  domain_scores: { skin_clinic: number; plastic_surgery: number; obesity_clinic: number };
}

export interface GrowthPoint {
  date: string;
  new_count: number;
  cumulative: number;
}

export interface RunLog {
  id: string;
  job_type: string;
  started_at: string;
  finished_at: string | null;
  total_attempted: number;
  success_count: number;
  failed_count: number;
  new_accounts_found: number;
  apify_calls_made: number;
  db_total_after: number | null;
  duration_seconds: number | null;
}

export interface QueueItem {
  status: string;
  job_type: string;
  count: number;
}

export interface Hashtag {
  hashtag: string;
  domain: string;
  last_crawled_at: string | null;
  crawl_count: number;
  new_accounts_found_last: number;
  total_accounts_found: number;
  is_exhausted: boolean;
  source: string;
}

export interface Influencer {
  id: string;
  handle: string;
  full_name: string | null;
  followers: number | null;
  follower_tier: string | null;
  engagement_rate: number | null;
  match_score_skin_clinic: number | null;
  match_score_plastic_surgery: number | null;
  match_score_obesity_clinic: number | null;
  treatment_tags: string[];
  region_tags: string[];
  status: string;
  last_scraped_at: string | null;
  anomaly_flag: boolean;
  quality_flags: string[];
}

export interface InfluencerList {
  total: number;
  page: number;
  size: number;
  items: Influencer[];
}

export interface GoalStats {
  goal: number;
  meaningful: number;
  progress_pct: number;
  enriched: number;
  pending_enrichment: number;
  total_collected: number;
  discarded: number;
  domain_breakdown: { skin_clinic: number; plastic_surgery: number; obesity_clinic: number };
}

export const api = {
  getGoal: () => get<GoalStats>("/stats/goal"),
  getStats: () => get<Stats>("/stats"),
  getDistribution: () => get<Distribution>("/stats/distribution"),
  getGrowth: (days = 30) => get<GrowthPoint[]>(`/stats/growth?days=${days}`),
  getRuns: () => get<RunLog[]>("/runs"),
  getQueue: () => get<QueueItem[]>("/queue"),
  getHashtags: () => get<Hashtag[]>("/hashtags"),
  getInfluencers: (page = 1, tier?: string, domain?: string) => {
    const params = new URLSearchParams({ page: String(page), size: "50" });
    if (tier) params.set("tier", tier);
    if (domain) params.set("domain", domain);
    return get<InfluencerList>(`/influencers?${params}`);
  },
  triggerDiscovery: () => post("/jobs/discovery"),
  triggerEnrichment: () => post("/jobs/enrichment"),
  triggerRefresh: (tier: string) => post(`/jobs/refresh/${tier}`),
};
