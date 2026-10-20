"use client";

import React, { FormEvent, useEffect, useMemo, useState } from "react";
import { MatchCard } from "@/components/MatchCard";
import { ResultDialog, type ResultAction } from "@/components/ResultDialog";
import { SwissOverview } from "@/components/SwissOverview";
import { getMatchDetail, getOverview, getTeams, importFile, loadDemoDataset, runSimulation } from "@/lib/api";
import { exportRows, percentage, rowsToCsv, scenarioChanged, setScenarioWinner } from "@/lib/helpers";
import type { Overview, PredictedMatch, Team, TournamentResult } from "@/lib/types";

type ViewKey = "overview" | "stage1" | "stage2" | "stage3" | "playoffs" | "teams" | "data";

const navigation: { key: ViewKey; label: string; icon: string; section?: string }[] = [
  { key: "overview", label: "Overview", icon: "◈", section: "TOURNAMENT" },
  { key: "stage1", label: "Stage 1", icon: "01" },
  { key: "stage2", label: "Stage 2", icon: "02" },
  { key: "stage3", label: "Stage 3", icon: "03" },
  { key: "playoffs", label: "Playoffs", icon: "⌁" },
  { key: "teams", label: "Teams", icon: "◎", section: "INTELLIGENCE" },
  { key: "data", label: "Method & data", icon: "⌘" },
];

function asLocalInput(value: Date) {
  const offset = value.getTimezoneOffset();
  return new Date(value.getTime() - offset * 60_000).toISOString().slice(0, 16);
}

function TeamMark({ name, small = false }: { name: string; small?: boolean }) {
  return <span className={`team-mark${small ? " team-mark-small" : ""}`}>{name.slice(0, 2).toUpperCase()}</span>;
}

function formatDate(value?: string | null) {
  if (!value) return "Date TBD";
  const date = new Date(`${value.slice(0, 10)}T12:00:00`);
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(date);
}

function formatTimestamp(value?: string | null) {
  if (!value) return "Not refreshed yet";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function downloadFile(name: string, contents: string, type: string) {
  const blob = new Blob([contents], { type });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

function projectedMatchesFor(result: TournamentResult) {
  return [
    ...Object.values(result.projected_path.stages).flatMap((stage) => stage.rounds.flatMap((round) => round.matches)),
    ...result.projected_path.playoffs.rounds.flatMap((round) => round.matches),
  ];
}

export default function Home() {
  const [view, setView] = useState<ViewKey>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [teams, setTeams] = useState<Team[]>([]);
  const [result, setResult] = useState<TournamentResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [simulating, setSimulating] = useState(false);
  const [error, setError] = useState("");
  const [simulations, setSimulations] = useState(2500);
  const [randomSeed, setRandomSeed] = useState(73);
  const [cutoff, setCutoff] = useState(() => asLocalInput(new Date()));
  const [draftOverrides, setDraftOverrides] = useState<Record<string, string>>({});
  const [appliedOverrides, setAppliedOverrides] = useState<Record<string, string>>({});
  const [selectedMatch, setSelectedMatch] = useState<PredictedMatch | null>(null);
  const [matchDetail, setMatchDetail] = useState<Record<string, unknown> | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [importKind, setImportKind] = useState<"json" | "teams" | "matches">("json");
  const [replaceDemo, setReplaceDemo] = useState(false);
  const [importStatus, setImportStatus] = useState("");
  const [importError, setImportError] = useState("");
  const [demoLoading, setDemoLoading] = useState(false);
  const [demoError, setDemoError] = useState("");
  const [resultEditor, setResultEditor] = useState<{ match: PredictedMatch; action: ResultAction } | null>(null);
  const [resultSaving, setResultSaving] = useState(false);
  const [resultNotice, setResultNotice] = useState("");

  const teamMap = useMemo(() => new Map(teams.map((team) => [team.id, team])), [teams]);
  const hasCompleteField = teams.length >= 32;
  const champion = result
    ? Object.entries(result.probabilities).sort((a, b) => b[1].champion.probability - a[1].champion.probability)[0]
    : undefined;
  const changedScenario = scenarioChanged(draftOverrides, appliedOverrides);
  const projectedMatches = result ? [
    ...Object.values(result.projected_path.stages).flatMap((stage) => stage.rounds.flatMap((round) => round.matches)),
    ...result.projected_path.playoffs.rounds.flatMap((round) => round.matches),
  ] : [];
  const confirmedResultCount = projectedMatches.filter((match) => match.result_status === "confirmed" || match.outcome_source === "completed_result").length;

  async function refreshBaseData() {
    const [overviewData, teamsData] = await Promise.all([getOverview(), getTeams()]);
    setOverview(overviewData);
    setTeams(teamsData);
    if (teamsData.length < 32) setResult(null);
    return { overviewData, teamsData };
  }

  async function executeSimulation(overrides = appliedOverrides) {
    setSimulating(true);
    setError("");
    try {
      const next = await runSimulation({
        simulations,
        seed: randomSeed,
        cutoff: new Date(cutoff).toISOString(),
        scenario_overrides: overrides,
      });
      setResult(next);
      setAppliedOverrides(overrides);
      setDraftOverrides(overrides);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Simulation could not be completed.");
    } finally {
      setSimulating(false);
      setLoading(false);
    }
  }

  useEffect(() => {
    let alive = true;
    Promise.all([getOverview(), getTeams()])
      .then(([overviewData, teamsData]) => {
        if (!alive) return;
        setOverview(overviewData);
        setTeams(teamsData);
        if (teamsData.length >= 32) void executeInitial();
        else setLoading(false);
      })
      .catch((cause: unknown) => {
        if (!alive) return;
        setError(cause instanceof Error ? cause.message : "The API could not be reached.");
        setLoading(false);
      });
    async function executeInitial() {
      try {
        setSimulating(true);
        const next = await runSimulation({
          simulations: 2500,
          seed: 73,
          cutoff: new Date().toISOString(),
          scenario_overrides: {},
        });
        if (alive) setResult(next);
      } catch (cause) {
        if (alive) setError(cause instanceof Error ? cause.message : "Simulation could not be completed.");
      } finally {
        if (alive) {
          setSimulating(false);
          setLoading(false);
        }
      }
    }
    return () => { alive = false; };
    // The first render uses the default reproducible run; later runs use the controls above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function openMatch(match: PredictedMatch) {
    setSelectedMatch(match);
    setMatchDetail(null);
    setDetailLoading(true);
    try {
      const detail = await getMatchDetail({
        team_a_id: match.team_a_id,
        team_b_id: match.team_b_id,
        best_of: match.best_of,
        cutoff: result?.cutoff ?? new Date().toISOString(),
      });
      setMatchDetail(detail);
    } catch (cause) {
      setMatchDetail({ detail_error: cause instanceof Error ? cause.message : "Could not load match details." });
    } finally {
      setDetailLoading(false);
    }
  }

  function changeScenario(match: PredictedMatch, winnerId: string) {
    setDraftOverrides((current) => setScenarioWinner(current, match.id, winnerId || null));
  }

  function manageMatchResult(match: PredictedMatch, action: ResultAction) {
    setResultNotice("");
    setResultEditor({ match, action });
  }

  function onMatchResultSaved(response: { message: string; result: TournamentResult; invalidated_result_ids: string[] }) {
    setResult(response.result);
    setAppliedOverrides(response.result.scenario.overrides);
    setDraftOverrides(response.result.scenario.overrides);
    const suffix = response.invalidated_result_ids.length
      ? ` ${response.invalidated_result_ids.length} downstream result(s) were cleared after confirmation.`
      : "";
    setResultNotice(`${response.message}${suffix}`);
    const updatedMatch = projectedMatchesFor(response.result).find((match) => match.id === resultEditor?.match.id);
    if (updatedMatch && selectedMatch?.id === updatedMatch.id) void openMatch(updatedMatch);
    setResultEditor(null);
    void getOverview().then(setOverview).catch(() => undefined);
  }

  function applyScenario() {
    void executeSimulation(draftOverrides);
  }

  function resetScenario() {
    setDraftOverrides({});
    setAppliedOverrides({});
    void executeSimulation({});
  }

  async function onImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.namedItem("dataset") as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) {
      setImportError("Choose a CSV or JSON file first.");
      return;
    }
    setImportError("");
    setImportStatus("Importing records…");
    try {
      const response = await importFile(file, importKind, replaceDemo);
      setImportStatus(`Imported: ${Object.entries(response.records_imported as Record<string, number>).filter(([, count]) => count).map(([key, count]) => `${count} ${key}`).join(", ") || "no new rows"}.`);
      const refreshed = await refreshBaseData();
      setDraftOverrides({});
      setAppliedOverrides({});
      if (refreshed.teamsData.length >= 32) await executeSimulation({});
      else setImportStatus((current) => `${current} Simulation will be available when all 32 participant records are loaded.`);
      input.value = "";
    } catch (cause) {
      setImportStatus("");
      setImportError(cause instanceof Error ? cause.message : "Import failed.");
    }
  }

  async function onLoadDemo() {
    setDemoError("");
    setDemoLoading(true);
    setError("");
    try {
      await loadDemoDataset();
      await refreshBaseData();
      setDraftOverrides({});
      setAppliedOverrides({});
      setSimulating(true);
      const next = await runSimulation({
        simulations,
        seed: randomSeed,
        cutoff: new Date(cutoff).toISOString(),
        scenario_overrides: {},
      });
      setResult(next);
      setDemoError("");
      setLoading(false);
    } catch (cause) {
      setDemoError(cause instanceof Error ? cause.message : "Could not load the demo dataset.");
    } finally {
      setDemoLoading(false);
      setSimulating(false);
    }
  }

  function exportCsv() {
    if (!result) return;
    downloadFile("singapore-major-predictions.csv", rowsToCsv(exportRows(result)), "text/csv;charset=utf-8");
  }

  function exportJson() {
    if (!result) return;
    downloadFile("singapore-major-predictions.json", JSON.stringify(result, null, 2), "application/json");
  }

  const matchesForStage = (slug: "stage1" | "stage2" | "stage3") => result?.projected_path.stages[slug];
  const firstStageRound = result?.projected_path.stages.stage1.rounds[0];
  const upcomingMatches = firstStageRound?.matches.slice(0, 4) ?? [];
  const playoffChampionName = teamMap.get(result?.projected_path.playoffs.champion_id ?? "")?.name ?? "—";

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <a className="brand" href="#overview" onClick={() => setView("overview")} aria-label="Major forecast home">
          <span className="brand-mark"><span>CS</span><b>2</b></span>
          <span className="brand-title">MAJOR<span>FORECAST</span></span>
        </a>

        <div className="event-switcher">
          <span className="event-switcher-label">ACTIVE EVENT</span>
          <strong>PGL Singapore</strong>
          <span>CS2 Major · 2026</span>
          <span className="live-dot"><i /> PREDICTION MODE</span>
        </div>

        <nav className="side-nav" aria-label="Tournament views">
          {navigation.map((item) => (
            <div key={item.key}>
              {item.section && <span className="nav-section">{item.section}</span>}
              <button className={`nav-item${view === item.key ? " active" : ""}`} onClick={() => setView(item.key)}>
                <span className={`nav-icon${item.icon.length > 1 ? " nav-number" : ""}`}>{item.icon}</span>
                <span>{item.label}</span>
                {item.key === "playoffs" && <span className="nav-count">8</span>}
              </button>
            </div>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="model-mini">
            <span className="model-mini-orb">✳</span>
            <div><b>Elo baseline</b><small>{result?.model.status === "trained_chronological_split" ? "Trained model active" : "Interpretable · untrained"}</small></div>
            <span className="model-mini-dot" />
          </div>
          <button className="data-source-mini" onClick={() => setView("data")}>
            <span className="source-mini-icon">▤</span>
            <span><b>{overview?.data.synthetic_records_present ? "Synthetic demo" : overview?.data.team_records ? "Participant data" : "No team data"}</b><small>Data & methodology ↗</small></span>
          </button>
          <div className="sidebar-footnote">Built for tournament analysis<br />Singapore · 2026</div>
        </div>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div className="mobile-brand"><span className="brand-mark"><span>CS</span><b>2</b></span><b>MAJOR FORECAST</b></div>
          <div className="breadcrumb"><span>EVENTS</span><i>/</i><strong>PGL MAJOR SINGAPORE 2026</strong></div>
          <div className="topbar-right">
            <span className={`source-pill${overview?.data.synthetic_records_present ? " source-synthetic" : ""}`}>
              <i /> {overview?.data.synthetic_records_present ? "DEMO DATA" : overview?.data.team_records ? "IMPORTED DATA" : "PARTICIPANTS TBD"}
            </span>
            <span className="topbar-divider" />
            <span className="topbar-date">25 NOV — 13 DEC 2026</span>
          </div>
        </header>

        <div className="content-wrap">
          <div className="page-title-row">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> COUNTER-STRIKE 2 · MAJOR SERIES</div>
              <h1>{viewTitle(view)}</h1>
              <p className="page-subtitle">{hasCompleteField ? "A model of the road to the Singapore Major trophy." : "The tournament structure is ready; team predictions await the confirmed field."}</p>
            </div>
            <div className="page-title-actions">
              <button className="quiet-button" onClick={exportCsv} disabled={!result} title="Export projected matches as CSV">↧ <span>CSV</span></button>
              <button className="quiet-button" onClick={exportJson} disabled={!result} title="Export full prediction run as JSON">↧ <span>JSON</span></button>
            </div>
          </div>

          <section className="sim-toolbar" aria-label="Simulation settings">
            <div className="toolbar-label"><span className="toolbar-icon">◉</span><div><b>SIMULATION SETTINGS</b><small>Reproducible tournament run</small></div></div>
            <label><span>RUNS</span><select value={simulations} onChange={(e) => setSimulations(Number(e.target.value))}>
              {[250, 1000, 2500, 5000, 10000].map((value) => <option key={value} value={value}>{value.toLocaleString()}</option>)}
            </select></label>
            <label><span>SEED</span><input aria-label="Random seed" type="number" min={0} value={randomSeed} onChange={(e) => setRandomSeed(Math.max(0, Number(e.target.value)))} /></label>
            <label className="cutoff-control"><span>DATA CUTOFF</span><input aria-label="Prediction cutoff" type="datetime-local" value={cutoff} onChange={(e) => setCutoff(e.target.value)} /></label>
            <button className="run-button" onClick={() => void executeSimulation()} disabled={simulating || loading || resultSaving || !hasCompleteField} title={!hasCompleteField ? "Load 32 participant records before simulating" : undefined}>
              <span>{simulating ? "◌" : "▶"}</span>{simulating ? "SIMULATING" : hasCompleteField ? "SIMULATE TOURNAMENT" : "FIELD NOT READY"}
            </button>
          </section>

          {result && <div className="result-summary" role="status">
            <span className="result-summary-mark">✓</span>
            <span><b>{confirmedResultCount} confirmed result{confirmedResultCount === 1 ? "" : "s"}</b><small>{Math.max(0, projectedMatches.length - confirmedResultCount)} remaining outcome{projectedMatches.length - confirmedResultCount === 1 ? " is" : "s are"} projected · confirmed winners are locked into recalculation</small></span>
          </div>}
          {resultNotice && <div className="success-banner" role="status">✓ {resultNotice}</div>}

          {changedScenario && <div className="scenario-banner">
            <div><b>UNAPPLIED SCENARIO</b><span>One or more unresolved match winners have been changed. Recompute the dependent pairings and probabilities.</span></div>
            <div className="scenario-actions"><button className="scenario-reset" onClick={resetScenario}>Reset</button><button className="scenario-apply" onClick={applyScenario} disabled={simulating}>Apply scenario ↗</button></div>
          </div>}

          {overview?.data.synthetic_records_present && <div className="demo-notice"><span>ⓘ</span><div><b>Demonstration mode</b><span>Teams and ratings are synthetic. Singapore participants and future match results are not being shown as confirmed.</span></div><button onClick={() => setView("data")}>About the data <span>↗</span></button></div>}

          {error && <div className="error-banner" role="alert"><b>Something needs attention</b><span>{error}</span>{!overview && <code>Start the API from backend/ and the Next.js app with npm run dev.</code>}</div>}

          {loading && <div className="loading-panel"><span className="spinner" /><b>Loading tournament model</b><span>Connecting to the local prediction service…</span></div>}

          {!loading && overview && (
            <>
              {view === "data" && <DataView
                overview={overview} result={result} importKind={importKind} setImportKind={setImportKind}
                replaceDemo={replaceDemo} setReplaceDemo={setReplaceDemo} onImport={onImport}
                importStatus={importStatus} importError={importError}
                onLoadDemo={onLoadDemo} demoLoading={demoLoading} demoError={demoError}
              />}
              {view !== "data" && !hasCompleteField && <FieldPendingView view={view} overview={overview} onNavigate={setView} />}
              {view === "overview" && hasCompleteField && <OverviewView
                overview={overview} teams={teams} result={result} teamMap={teamMap} champion={champion}
                playoffChampionName={playoffChampionName}
                upcomingMatches={upcomingMatches} onOpenMatch={openMatch} onNavigate={setView}
              />}
              {(view === "stage1" || view === "stage2" || view === "stage3") && result && <StageView
                slug={view} overview={overview} stage={matchesForStage(view)} result={result} teamMap={teamMap}
                draftOverrides={draftOverrides} onScenarioChange={changeScenario} onOpenMatch={openMatch} onManageResult={manageMatchResult}
              />}
              {view === "playoffs" && result && <PlayoffsView
                result={result} teamMap={teamMap} draftOverrides={draftOverrides}
                onScenarioChange={changeScenario} onOpenMatch={openMatch} onManageResult={manageMatchResult}
              />}
              {view === "teams" && hasCompleteField && result && <TeamsView teams={teams} result={result} />}
            </>
          )}

          {selectedMatch && <MatchDetailPanel
            match={selectedMatch} detail={matchDetail} loading={detailLoading} teamMap={teamMap}
            probabilities={result?.probabilities} onClose={() => setSelectedMatch(null)} onManageResult={manageMatchResult}
          />}

          {resultEditor && <ResultDialog
            match={resultEditor.match} teamMap={teamMap} action={resultEditor.action} onClose={() => !resultSaving && setResultEditor(null)}
            onBusyChange={setResultSaving} onSaved={onMatchResultSaved}
          />}

          <footer className="page-footer">
            <span>◈ <b>MAJOR FORECAST</b> <i>·</i> independent analysis</span>
            <span>Predictions are estimates <i>·</i> {result?.simulations.toLocaleString() ?? "—"} simulations <i>·</i> seed {result?.seed ?? "—"}</span>
          </footer>
        </div>
      </main>
    </div>
  );
}

function viewTitle(view: ViewKey) {
  const titles: Record<ViewKey, string> = {
    overview: "Tournament overview",
    stage1: "Stage 1 · Swiss",
    stage2: "Stage 2 · Swiss",
    stage3: "Stage 3 · Swiss",
    playoffs: "The playoff bracket",
    teams: "Team power index",
    data: "Methodology & data",
  };
  return titles[view];
}

function FieldPendingView({ view, overview, onNavigate }: { view: ViewKey; overview: Overview; onNavigate: (view: ViewKey) => void }) {
  const views: Record<ViewKey, { label: string; slots: string; detail: string }> = {
    overview: { label: "Tournament field", slots: "32 team identities pending", detail: "The official participant list has not been published. This view keeps the tournament structure visible without assigning invented teams or match probabilities." },
    stage1: { label: "Stage 1 entrants", slots: "16 direct-entry slots · TBD", detail: "The Stage 1 invite names and opening fixtures are not available yet. Swiss pairings will appear once the real entrant list is imported." },
    stage2: { label: "Stage 2 entrants", slots: "8 direct slots + 8 Stage 1 advancers", detail: "No team names or matchups are assigned to these slots. Advancers will be filled from confirmed Stage 1 results or a clearly labeled simulation." },
    stage3: { label: "Stage 3 entrants", slots: "8 direct slots + 8 Stage 2 advancers", detail: "No team names or matchups are assigned to these slots. The projected bracket will populate after real entrants and earlier results are available." },
    playoffs: { label: "Playoff bracket", slots: "8 qualifiers · TBD", detail: "Quarterfinals through the grand final are held empty until Stage 3 qualifiers can be determined." },
    teams: { label: "Team power index", slots: "No participant records loaded", detail: "The leaderboard will use stable team IDs and imported records. It will not invent team identities or team ratings." },
    data: { label: "Method & data", slots: "Participant data is pending", detail: "Import official entrant data when available, or load the optional synthetic demo." },
  };
  const current = views[view];
  const loaded = overview.data.team_records;
  return <div className="field-pending-view">
    <section className="field-pending-hero">
      <div className="pending-orbit" aria-hidden="true"><span>CS</span><b>2</b></div>
      <div className="pending-copy">
        <span className="pending-status"><i /> OFFICIAL PARTICIPANT LIST NOT PUBLISHED</span>
        <span className="section-overline">{current.label.toUpperCase()}</span>
        <h2>{loaded ? `${loaded} of 32 team records loaded` : "The team slots are still unconfirmed."}</h2>
        <p>{current.detail}</p>
        <div className="pending-cta-row">
          <button className="run-button" onClick={() => onNavigate("data")}>Import participant data <span>↗</span></button>
          <a href="https://www.hltv.org/major/invites" target="_blank" rel="noreferrer">Check the invite tracker ↗</a>
        </div>
        <small className="pending-cutoff">HLTV lists the Valve Regional Standings invite cutoff as November 2, 2026. Status shown here was checked October 19, 2026.</small>
      </div>
    </section>
    <div className="pending-slot-summary">
      <div className="section-heading-row"><div><span className="section-overline">TOURNAMENT STRUCTURE</span><h2>Slots stay blank until teams are confirmed</h2></div><span className="soft-badge">NO SYNTHETIC TEAMS IN THIS VIEW</span></div>
      <div className="pending-phase-grid">
        <div className="pending-phase"><span>01 · SWISS</span><b>Stage 1</b><strong>16 <i>team slots</i></strong><small>8 advance · team names TBD</small></div>
        <div className="pending-phase"><span>02 · SWISS</span><b>Stage 2</b><strong>8 + 8 <i>slots</i></strong><small>Direct entrants + Stage 1 advancers</small></div>
        <div className="pending-phase"><span>03 · SWISS</span><b>Stage 3</b><strong>8 + 8 <i>slots</i></strong><small>Direct entrants + Stage 2 advancers</small></div>
        <div className="pending-phase pending-phase-playoffs"><span>04 · ELIMINATION</span><b>Playoffs</b><strong>8 <i>qualifiers</i></strong><small>Quarterfinals to grand final</small></div>
      </div>
      {view !== "overview" && <div className="pending-current"><span className="section-overline">SELECTED VIEW · {viewTitle(view).toUpperCase()}</span><b>{current.slots}</b><span>Predictions and simulated pairings are paused until a complete field is loaded.</span></div>}
    </div>
  </div>;
}

function OverviewView(props: {
  overview: Overview;
  teams: Team[];
  result: TournamentResult | null;
  teamMap: Map<string, Team>;
  champion: [string, TournamentResult["probabilities"][string]] | undefined;
  playoffChampionName: string;
  upcomingMatches: PredictedMatch[];
  onOpenMatch: (match: PredictedMatch) => void;
  onNavigate: (view: ViewKey) => void;
}) {
  const { overview, teams, result, teamMap, champion, playoffChampionName, upcomingMatches } = props;
  const pathChampion = result?.projected_path.playoffs.champion_id;
  const finalMatch = result?.projected_path.playoffs.rounds[2]?.matches[0];
  const finalConfirmed = finalMatch?.result_status === "confirmed" || finalMatch?.outcome_source === "completed_result";
  const pathProbability = pathChampion ? result?.probabilities[pathChampion]?.champion.probability : 0;
  const leaderboard = Object.entries(result?.probabilities ?? {}).sort((a, b) => b[1].champion.probability - a[1].champion.probability).slice(0, 5);
  return (
    <div className="overview-content">
      <div className="overview-hero-grid">
        <section className="champion-card">
          <div className="champion-card-glow" />
          <div className="champion-card-top"><span className="hero-kicker"><i /> {finalConfirmed ? "ACTUAL CHAMPION" : "PROJECTED CHAMPION"}</span><span className="hero-trophy">✦</span></div>
          <div className="champion-team-row">
            <TeamMark name={playoffChampionName} />
            <div><strong>{playoffChampionName}</strong><span>{teamMap.get(pathChampion ?? "")?.region ?? "Projected from Stage 3"} <i>·</i> Seed #{teamMap.get(pathChampion ?? "")?.seed ?? "—"}</span></div>
          </div>
          <div className="champion-card-bottom">
            <div className="champion-odds"><strong>{percentage(pathProbability)}</strong><span>championship probability</span></div>
            <div className="hero-route"><span>PROJECTED PATH</span><b>QF <i>→</i> SF <i>→</i> FINAL</b></div>
          </div>
          {champion && champion[0] !== pathChampion && <div className="champion-alt-favorite">Highest Monte Carlo title odds: <b>{teamMap.get(champion[0])?.name ?? "TBD"}</b> · {percentage(champion[1].champion.probability)}</div>}
          <div className="champion-foot"><span>Scenario: {result?.scenario.active ? "user override" : "model picks"}</span><span>{result?.simulations.toLocaleString() ?? "—"} tournament runs</span></div>
        </section>

        <section className="panel odds-panel">
          <div className="panel-heading"><div><span className="section-overline">TITLE OUTLOOK</span><h2>Championship odds</h2></div><span className="panel-icon">↗</span></div>
          <div className="odds-list">
            {leaderboard.map(([teamId, probability], index) => {
              const team = teamMap.get(teamId);
              if (!team) return null;
              return <div className="odds-row" key={teamId}>
                <span className="odds-rank">{String(index + 1).padStart(2, "0")}</span>
                <TeamMark name={team.name} small />
                <div className="odds-name"><b>{team.name}</b><small>{team.region ?? "Region unknown"}</small></div>
                <div className="odds-bar-wrap"><div className="odds-track"><i style={{ width: `${Math.max(2, probability.champion.probability * 100)}%` }} /></div></div>
                <strong className="odds-value">{percentage(probability.champion.probability)}</strong>
              </div>;
            })}
            {!leaderboard.length && <div className="empty-inline">Run a simulation to see title probabilities.</div>}
          </div>
          <div className="odds-foot"><span>Estimated across full tournament paths</span><span>± sampling error</span></div>
        </section>
      </div>

      <section className="quick-stats">
        <StatBlock icon="◷" label="EVENT WINDOW" value="25 Nov — 13 Dec" detail="2026 · Singapore" />
        <StatBlock icon="◉" label="TOURNAMENT FIELD" value={`${overview.event ? 32 : teams.length} teams`} detail="16 · 8 · 8 across the stages" />
        <StatBlock icon="⌁" label="SWISS ROUNDS" value="Up to 5 per stage" detail="3 wins advance · 3 losses out*" />
        <StatBlock icon="✧" label="PLAYOFFS" value="8-team bracket" detail="Bo3 · Bo3 · Bo5 final" />
      </section>

      <div className="section-heading-row"><div><span className="section-overline">PATH TO THE TROPHY</span><h2>Four phases. One champion.</h2></div><button className="text-link" onClick={() => props.onNavigate("stage1")}>Explore stages <span>↗</span></button></div>
      <div className="phase-track">
        {[
          { slug: "stage1", number: "01", name: "Stage 1", dates: "24–27 NOV", note: "16 teams · 8 advance", tint: "mint" },
          { slug: "stage2", number: "02", name: "Stage 2", dates: "28 NOV–01 DEC", note: "8 + 8 direct entrants", tint: "blue" },
          { slug: "stage3", number: "03", name: "Stage 3", dates: "02–06 DEC", note: "8 + 8 direct entrants", tint: "violet" },
          { slug: "playoffs", number: "04", name: "Playoffs", dates: "10–13 DEC", note: "8 teams · single elimination", tint: "gold" },
        ].map((phase, index) => <button className={`phase-card phase-${phase.tint}`} key={phase.slug} onClick={() => props.onNavigate(phase.slug as ViewKey)}>
          <span className="phase-number">{phase.number}</span><span className="phase-arrow">{index < 3 ? "→" : "✦"}</span>
          <b>{phase.name}</b><span className="phase-dates">{phase.dates}</span><span className="phase-note">{phase.note}</span>
          <span className="phase-rule" />
        </button>)}
      </div>

      <div className="overview-lower-grid">
        <section className="panel upcoming-panel">
          <div className="panel-heading"><div><span className="section-overline">PROJECTED PATH · STAGE 1</span><h2>Opening matchups</h2></div><span className="soft-badge">NOT OFFICIAL FIXTURES</span></div>
          <p className="panel-caption">These openers use configured seeds. Later pairings depend on each round's results.</p>
          <div className="opening-list">
            {upcomingMatches.map((match) => <OpeningMatch key={match.id} match={match} teamMap={teamMap} onOpen={() => props.onOpenMatch(match)} />)}
            {!upcomingMatches.length && <div className="empty-inline">Opening projections will appear after a successful simulation.</div>}
          </div>
        </section>

        <section className="panel data-health-panel">
          <div className="panel-heading"><div><span className="section-overline">MODEL & DATA</span><h2>Run health</h2></div><span className="health-glyph">⌁</span></div>
          <div className="health-row"><span>Prediction engine</span><b><i className="status-led" />{result?.model.name ?? "Elo baseline"}</b></div>
          <div className="health-row"><span>Training status</span><b>{result?.model.status === "trained_chronological_split" ? "Chronological validation" : "Baseline · not trained"}</b></div>
          <div className="health-row"><span>Match records available</span><b>{overview.data.match_records_available.toLocaleString()}</b></div>
          <div className="health-row"><span>Official event results</span><b>{overview.data.completed_event_results}</b></div>
          <div className="health-row"><span>Last successful refresh</span><b>{formatTimestamp(overview.data.last_successful_refresh)}</b></div>
          <div className="health-note"><span>ⓘ</span><p>{overview.data.synthetic_records_present ? "This run uses synthetic team ratings. No real CS match or player records are loaded." : "Imported statistics outside the Elo baseline are not currently used as prediction features."}</p></div>
        </section>
      </div>

      <div className="overview-footnote"><span>*</span> Three-win advancement and three-loss elimination are provisional settings based on the event stage tracker; exact organizer rules remain unconfirmed.</div>
    </div>
  );
}

function StatBlock({ icon, label, value, detail }: { icon: string; label: string; value: string; detail: string }) {
  return <div className="stat-block"><span className="stat-icon">{icon}</span><div><span className="stat-label">{label}</span><b>{value}</b><small>{detail}</small></div></div>;
}

function OpeningMatch({ match, teamMap, onOpen }: { match: PredictedMatch; teamMap: Map<string, Team>; onOpen: () => void }) {
  const a = teamMap.get(match.team_a_id);
  const b = teamMap.get(match.team_b_id);
  return <button className="opening-match" onClick={onOpen}>
    <span className="opening-round">R1</span>
    <span className="opening-team"><TeamMark name={a?.name ?? "TBD"} small /><b>{a?.name ?? match.team_a_id}</b><small>#{a?.seed ?? "—"}</small></span>
    <span className="opening-vs"><i>VS</i><small>BO{match.best_of}</small></span>
    <span className="opening-team opening-team-right"><small>#{b?.seed ?? "—"}</small><b>{b?.name ?? match.team_b_id}</b><TeamMark name={b?.name ?? "TBD"} small /></span>
    <span className="opening-odds">{percentage(match.prediction.team_a_probability)} <i>/</i> {percentage(match.prediction.team_b_probability)}</span>
    <span className="opening-chevron">↗</span>
  </button>;
}

function StageView(props: {
  slug: "stage1" | "stage2" | "stage3";
  overview: Overview;
  stage: TournamentResult["projected_path"]["stages"]["stage1"] | undefined;
  result: TournamentResult;
  teamMap: Map<string, Team>;
  draftOverrides: Record<string, string>;
  onScenarioChange: (match: PredictedMatch, winnerId: string) => void;
  onOpenMatch: (match: PredictedMatch) => void;
  onManageResult: (match: PredictedMatch, action: ResultAction) => void;
}) {
  const { slug, overview, stage, result, teamMap, draftOverrides, onScenarioChange, onOpenMatch, onManageResult } = props;
  const config = overview.stages.find((item) => item.slug === slug);
  if (!stage || !config) return <div className="empty-state">No projected data for this stage yet.</div>;
  const advancementField = slug === "stage1" ? "stage1_advance" : slug === "stage2" ? "stage2_advance" : "playoffs";
  const latestRound = stage.rounds.at(-1);
  const leaders = [...(latestRound?.standings ?? [])].sort((a, b) => (b.wins ?? 0) - (a.wins ?? 0) || (a.losses ?? 0) - (b.losses ?? 0) || a.seed - b.seed);
  const matchCount = stage.rounds.reduce((total, round) => total + round.matches.length, 0);
  return <div className="stage-view">
    <div className="stage-hero-row">
      <div className="stage-hero-card">
        <div className="stage-hero-orb">{slug === "stage1" ? "01" : slug === "stage2" ? "02" : "03"}</div>
        <div><span className="section-overline">{formatDate(config.start_date)} — {formatDate(config.end_date)} · 2026</span><h2>{config.name} <span>Swiss system</span></h2><p>{config.entrants} teams enter this round. Pairings are generated from current records, so the stage has no permanent knockout bracket.</p></div>
        <span className="provisional-tag">PROVISIONAL RULES</span>
      </div>
      <div className="stage-stat-card"><span>ADVANCING</span><b>{config.advances} <i>/</i> {config.entrants}</b><small>to the next phase</small></div>
      <div className="stage-stat-card"><span>PROJECTED ROUNDS</span><b>{stage.rounds.length} <i>/</i> 5</b><small>up to five in Swiss</small></div>
      <div className="stage-stat-card"><span>SIMULATED SERIES</span><b>{matchCount}</b><small>one coherent path</small></div>
    </div>

    <div className="notice-strip"><span>ⓘ</span><div><b>Pairings are conditional</b><span>Each next-round pairing is generated after the previous simulated results. These cards show one coherent model-projected path, not confirmed fixtures.</span></div></div>

    <SwissOverview stage={stage} teamMap={teamMap} />

    <div className="stage-layout">
      <div className="rounds-column">
        <div className="subsection-title"><div><span className="section-overline">PROJECTED MATCHES</span><h2>Round by round</h2></div><span className="soft-badge">{result.simulations.toLocaleString()} RUNS FOR ODDS</span></div>
        {stage.rounds.map((round) => <section className="round-section" key={round.round_no}>
          <div className="round-heading"><span className="round-chip">R{String(round.round_no).padStart(2, "0")}</span><div><b>Round {round.round_no}</b><small>{round.matches.length} pairings <i>·</i> seeded Swiss groupings</small></div><span className="round-line" /></div>
          <div className="match-grid">{round.matches.map((match) => <MatchCard key={match.id} match={match} teamMap={teamMap} draftOverrides={draftOverrides} onScenarioChange={onScenarioChange} onOpen={() => onOpenMatch(match)} onManageResult={onManageResult} />)}</div>
        </section>)}
        <div className="projection-end"><span>✓</span><div><b>{stage.advanced.length} teams projected to advance</b><span>Results flow into the next stage's direct-entry field.</span></div><div className="advance-avatars">{stage.advanced.slice(0, 5).map((team) => <TeamMark key={team.id} name={team.name} small />)}<i>+{Math.max(0, stage.advanced.length - 5)}</i></div></div>
      </div>

      <aside className="standings-column">
        <section className="panel standings-panel">
          <div className="panel-heading"><div><span className="section-overline">AFTER PROJECTED ROUND {stage.rounds.length}</span><h2>Standings</h2></div><span className="panel-icon">≋</span></div>
          <div className="standings-header"><span>SEED / TEAM</span><span>W–L</span><span>ADVANCE</span></div>
          {leaders.map((team, index) => {
            const odds = result.probabilities[team.id]?.[advancementField].probability ?? 0;
            const status = stage.advanced.some((item) => item.id === team.id) ? "ADV" : stage.eliminated.some((item) => item.id === team.id) ? "OUT" : "ACTIVE";
            return <div className={`standing-row${status === "ADV" ? " standing-advance" : ""}${status === "OUT" ? " standing-out" : ""}`} key={team.id}>
              <span className="standing-team"><i>{String(index + 1).padStart(2, "0")}</i><TeamMark name={team.name} small /><b>{team.name}</b></span>
              <span className="standing-record">{team.wins}–{team.losses}</span>
              <span className="standing-odds">{percentage(odds)}<small>{status}</small></span>
            </div>;
          })}
          <div className="standings-foot"><span>Advancement probability is unconditional</span><span>Based on full tournament runs</span></div>
        </section>
        <section className="panel format-panel">
          <div className="panel-heading"><div><span className="section-overline">CONFIGURED FORMAT</span><h2>Stage rules</h2></div><span className="format-symbol">⌁</span></div>
          <div className="format-rule"><b>3 wins</b><span>advance to the next stage</span></div>
          <div className="format-rule"><b>3 losses</b><span>eliminated from the event</span></div>
          <div className="format-rule"><b>{slug === "stage3" ? "Best-of-three" : "BO1 openers · BO3 deciders"}</b><span>{slug === "stage3" ? "all Stage 3 series" : "progression and elimination matches"}</span></div>
          <div className="format-warning"><span>!</span><p>Three wins / three losses and pairing details are configured assumptions pending organizer rulebook confirmation.</p></div>
        </section>
      </aside>
    </div>
  </div>;
}

function PlayoffsView(props: {
  result: TournamentResult;
  teamMap: Map<string, Team>;
  draftOverrides: Record<string, string>;
  onScenarioChange: (match: PredictedMatch, winnerId: string) => void;
  onOpenMatch: (match: PredictedMatch) => void;
  onManageResult: (match: PredictedMatch, action: ResultAction) => void;
}) {
  const { result, teamMap, draftOverrides, onScenarioChange, onOpenMatch, onManageResult } = props;
  const rounds = result.projected_path.playoffs.rounds;
  const champion = teamMap.get(result.projected_path.playoffs.champion_id);
  const finalists = rounds[2]?.matches[0];
  const titleLeader = Object.entries(result.probabilities).sort((a, b) => b[1].champion.probability - a[1].champion.probability)[0];
  const titleLeaderTeam = teamMap.get(titleLeader?.[0] ?? "");
  return <div className="playoffs-view">
    <div className="playoff-intro">
      <div><span className="section-overline">DECEMBER 10 — 13 · SINGAPORE INDOOR STADIUM</span><h2>Eight survive. One lifts the trophy.</h2><p>Projected bracket from the Stage 3 Swiss outcomes. Conditional match odds are shown on each card; title odds account for every simulated path.</p></div>
      <div className="playoff-champion"><span>{finalists?.result_status === "confirmed" || finalists?.outcome_source === "completed_result" ? "ACTUAL CHAMPION" : "PROJECTED PATH WINNER"}</span><div><TeamMark name={champion?.name ?? "TBD"} /><b>{champion?.name ?? "TBD"}</b></div><strong>{percentage(result.probabilities[champion?.id ?? ""]?.champion.probability)}</strong></div>
    </div>
    <div className="bracket-caption"><span><i className="bracket-dot dot-confirmed" /> Confirmed result / fixture</span><span><i className="bracket-dot dot-projected" /> Model projected matchup</span><span className="bracket-assumption">Bracket order uses provisional seed ranking</span></div>
    <div className="bracket-board">
      <BracketColumn title="QUARTERFINALS" subtitle="8 → 4" matches={rounds[0]?.matches ?? []} teamMap={teamMap} draftOverrides={draftOverrides} onScenarioChange={onScenarioChange} onOpenMatch={onOpenMatch} onManageResult={onManageResult} className="bracket-qf" />
      <div className="bracket-connector"><span /><span /><span /><span /></div>
      <BracketColumn title="SEMIFINALS" subtitle="4 → 2" matches={rounds[1]?.matches ?? []} teamMap={teamMap} draftOverrides={draftOverrides} onScenarioChange={onScenarioChange} onOpenMatch={onOpenMatch} onManageResult={onManageResult} className="bracket-sf" />
      <div className="bracket-connector bracket-connector-final"><span /><span /></div>
      <BracketColumn title="GRAND FINAL" subtitle="2 → 1 · BEST OF 5" matches={rounds[2]?.matches ?? []} teamMap={teamMap} draftOverrides={draftOverrides} onScenarioChange={onScenarioChange} onOpenMatch={onOpenMatch} onManageResult={onManageResult} className="bracket-final" />
    </div>
    <div className="playoff-bottom-grid">
      <section className="panel finalists-panel"><span className="section-overline">PROJECTED GRAND FINAL</span><h3>{finalists ? `${teamMap.get(finalists.team_a_id)?.name ?? "TBD"} <i>vs</i> ${teamMap.get(finalists.team_b_id)?.name ?? "TBD"}` : "Finalists take shape after the semifinals"}</h3><p>{finalists ? `Conditional series probability: ${percentage(finalists.prediction.team_a_probability)} — ${percentage(finalists.prediction.team_b_probability)}. This is not the same as either team's probability of reaching the final.` : "The bracket is recalculated from the projected Stage 3 qualifiers."}</p></section>
      <section className="panel champion-prob-panel"><span className="section-overline">CHAMPIONSHIP PROBABILITY LEADER</span><div><TeamMark name={titleLeaderTeam?.name ?? "TBD"} /><b>{titleLeaderTeam?.name ?? "TBD"}</b><strong>{percentage(titleLeader?.[1].champion.probability)}</strong></div><p>Full-tournament estimate across {result.simulations.toLocaleString()} runs.</p></section>
    </div>
  </div>;
}

function BracketColumn(props: {
  title: string;
  subtitle: string;
  matches: PredictedMatch[];
  teamMap: Map<string, Team>;
  draftOverrides: Record<string, string>;
  onScenarioChange: (match: PredictedMatch, winnerId: string) => void;
  onOpenMatch: (match: PredictedMatch) => void;
  onManageResult: (match: PredictedMatch, action: ResultAction) => void;
  className: string;
}) {
  return <section className={`bracket-column ${props.className}`}>
    <div className="bracket-column-heading"><div><b>{props.title}</b><span>{props.subtitle}</span></div><i>{props.matches.length} SERIES</i></div>
    <div className="bracket-match-stack">
      {props.matches.map((match) => <MatchCard key={match.id} match={match} teamMap={props.teamMap} draftOverrides={props.draftOverrides} onScenarioChange={props.onScenarioChange} onOpen={() => props.onOpenMatch(match)} onManageResult={props.onManageResult} />)}
      {!props.matches.length && <div className="empty-bracket">Projected qualifiers appear after Stage 3.</div>}
    </div>
  </section>;
}

function TeamsView({ teams, result }: { teams: Team[]; result: TournamentResult }) {
  const ranked = [...teams].sort((a, b) => (result.probabilities[b.id]?.champion.probability ?? 0) - (result.probabilities[a.id]?.champion.probability ?? 0));
  return <div className="teams-view">
    <div className="teams-intro"><div><span className="section-overline">32 CONFIGURED SLOTS</span><h2>Power index</h2><p>Sorted by full-tournament championship probability. Stage advancement values come from Monte Carlo counts, not the single projected route.</p></div><span className="power-index-mark">ELO<br /><b>↑</b></span></div>
    <div className="team-table-wrap"><table className="team-table"><thead><tr><th>RANK / TEAM</th><th>ENTRY</th><th>BASELINE ELO</th><th>REACH STAGE 2</th><th>REACH STAGE 3</th><th>REACH PLAYOFFS</th><th>REACH FINAL</th><th>WIN TITLE</th></tr></thead>
      <tbody>{ranked.map((team, index) => {
        const p = result.probabilities[team.id];
        const entry = team.direct_entries?.find((item) => item.stage === "stage1" || item.stage === "stage2" || item.stage === "stage3")?.stage.replace("stage", "Stage ") ?? "—";
        return <tr key={team.id}>
          <td><span className="team-rank">{String(index + 1).padStart(2, "0")}</span><TeamMark name={team.name} small /><span className="team-table-name"><b>{team.name}</b><small>{team.region ?? "Region unknown"}{team.synthetic ? " · synthetic" : ""}</small></span></td>
          <td><span className="entry-tag">{entry}</span></td>
          <td>{team.elo_rating?.toFixed(0) ?? "—"}<small className="table-muted">{team.rating_source ?? "No imported rating"}</small></td>
          <td>{percentage(p?.stage1_advance.probability)}</td><td>{percentage(p?.stage2_advance.probability)}</td><td>{percentage(p?.playoffs.probability)}</td><td>{percentage(p?.final.probability)}</td>
          <td><b className="title-odds-cell">{percentage(p?.champion.probability)}</b></td>
        </tr>;
      })}</tbody></table></div>
    <div className="teams-footnote"><span>ⓘ</span><p>Stage 2 and Stage 3 probabilities represent reaching those phases, including direct-entry slots. Synthetic Elo values are only for the demonstration dataset.</p></div>
  </div>;
}

function DataView(props: {
  overview: Overview;
  result: TournamentResult | null;
  importKind: "json" | "teams" | "matches";
  setImportKind: (value: "json" | "teams" | "matches") => void;
  replaceDemo: boolean;
  setReplaceDemo: (value: boolean) => void;
  onImport: (event: FormEvent<HTMLFormElement>) => void;
  onLoadDemo: () => void;
  demoLoading: boolean;
  demoError: string;
  importStatus: string;
  importError: string;
}) {
  const { overview, result } = props;
  return <div className="data-view">
    <div className="data-intro"><div><span className="section-overline">TRANSPARENCY BY DESIGN</span><h2>Know what went into the forecast.</h2><p>The official participant list is not published yet, so the default view contains no invented teams or team-specific predictions. Import the real field when it is announced, or load the optional synthetic demo below.</p></div><span className="data-seal">DATA<br /><b>01</b></span></div>
    <div className="data-grid">
      <section className="panel data-source-card">
        <div className="panel-heading"><div><span className="section-overline">ACTIVE PROVIDER</span><h2>Source status</h2></div><span className="source-glyph">▤</span></div>
        <div className="provider-active"><span className="provider-icon">◉</span><div><b>{overview.data.active_source}</b><small>{overview.data.synthetic_records_present ? "Synthetic demonstration records are present" : overview.data.team_records === 0 ? "No participant records loaded" : "User-supplied participant records loaded"}</small></div><span className="provider-badge">{overview.data.synthetic_records_present ? "DEMO" : overview.data.team_records === 0 ? "PENDING" : "IMPORTED"}</span></div>
        <div className="source-stat-grid"><div><span>Match records</span><b>{overview.data.match_records_available}</b></div><div><span>Event results</span><b>{overview.data.completed_event_results}</b></div><div><span>Teams</span><b>{overview.data.team_records}</b></div><div><span>Last refresh</span><b>{formatTimestamp(overview.data.last_successful_refresh)}</b></div></div>
        <div className="provider-note"><span>ⓘ</span><p>HLTV’s terms prohibit automated data mining and scraping. This app does not request HLTV pages or assume a public API exists. Import data you are permitted to use; the JSON format supports maps, vetoes, player statistics, dated rosters and match history. <a href="https://www.hltv.org/terms" target="_blank" rel="noreferrer">HLTV terms ↗</a></p></div>
      </section>
      <section className="panel import-card">
        <div className="panel-heading"><div><span className="section-overline">BRING YOUR OWN DATA</span><h2>Import CSV or JSON</h2></div><span className="import-glyph">↥</span></div>
        <p className="import-description">Imports are stored in local SQLite. JSON accepts match history, map results and vetoes, player ratings and dated rosters; eligible signals feed match probabilities. Imported completed event results are locked into every simulation.</p>
        <form onSubmit={props.onImport} className="import-form">
          <label className="import-kind"><span>FILE TYPE</span><select value={props.importKind} onChange={(event) => props.setImportKind(event.target.value as "json" | "teams" | "matches")}><option value="json">JSON bundle</option><option value="teams">Teams CSV</option><option value="matches">Matches CSV</option></select></label>
          <label className="file-drop"><input name="dataset" type="file" accept={props.importKind === "json" ? ".json,application/json" : ".csv,text/csv"} /><span className="file-drop-icon">＋</span><b>Choose a file to import</b><small>{props.importKind === "json" ? "JSON export bundle" : `${props.importKind} CSV template`}</small></label>
          {overview.data.synthetic_records_present && <label className="replace-demo"><input type="checkbox" checked={props.replaceDemo} onChange={(event) => props.setReplaceDemo(event.target.checked)} /><span><b>Replace synthetic demo teams</b><small>Use this when importing a complete real roster. Existing user imports are retained.</small></span></label>}
          <button className="import-button" type="submit">Import records <span>↗</span></button>
          {props.importStatus && <div className="import-feedback">✓ {props.importStatus}</div>}
          {props.importError && <div className="import-error" role="alert">{props.importError}</div>}
        </form>
        <div className="template-links"><span>Templates</span><a href="/templates/teams.csv" download>teams.csv ↧</a><a href="/templates/matches.csv" download>matches.csv ↧</a><a href="/templates/import.json" download>import.json ↧</a></div>
      </section>
    </div>

    {overview.data.team_records === 0 && overview.data.match_records_available === 0 && <section className="demo-loader panel">
      <div><span className="section-overline">OPTIONAL SANDBOX</span><h2>Explore with sample teams</h2><p>This loads the clearly labeled synthetic tournament from the project files. It is useful for trying Swiss progression, scenarios, and the playoff bracket; it does not represent real Major participants or results.</p></div>
      <button className="import-button" onClick={props.onLoadDemo} disabled={props.demoLoading}>{props.demoLoading ? "Loading demo…" : "Load synthetic demo tournament"}<span>↗</span></button>
      {props.demoError && <div className="import-error" role="alert">{props.demoError}</div>}
    </section>}

    <div className="data-section-heading"><span className="section-overline">EVENT CONFIGURATION</span><h2>Confirmed details & open questions</h2></div>
    <div className="confirmation-grid">
      <section className="confirmation-card confirmed"><span className="confirmation-status">CONFIRMED BY ORGANIZER</span><b>{overview.event.name}</b><p>PGL lists November 25–December 13, 2026; 32 teams; three competitive stages; and a best-of-five grand final at Singapore Indoor Stadium.</p><a href={overview.official_source_urls[0]} target="_blank" rel="noreferrer">PGL event page ↗</a></section>
      <section className="confirmation-card review"><span className="confirmation-status">SCHEDULE NEEDS RECONCILIATION</span><b>Published dates differ</b><p>HLTV lists Stage 1 starting November 24, one day before PGL's published event window. This app shows the stage calendar as a provisional third-party schedule.</p><a href="https://www.hltv.org/major/" target="_blank" rel="noreferrer">HLTV coverage hub ↗</a></section>
      <section className="confirmation-card review"><span className="confirmation-status">NOT PUBLISHED</span><b>Participants, pairing & seeds</b><p>As of October 19, the invite tracker still lists unfilled VRS invite slots. The app therefore leaves all team identities blank until official invitations are announced.</p><a href="https://www.hltv.org/major/invites" target="_blank" rel="noreferrer">HLTV invites tracker ↗</a></section>
      <section className="confirmation-card review"><span className="confirmation-status">PROVISIONAL SIMULATOR RULES</span><b>Swiss thresholds & playoff order</b><p>The 3–win / 3–loss cutoffs, same-record pairing, rematch policy, and 1v8/4v5 bracket seeding are explicit assumptions, not claimed organizer rules.</p><span className="inline-note">Edit stage configuration in backend/data/demo_seed.json</span></section>
    </div>

    <div className="data-section-heading"><span className="section-overline">MODEL CARD</span><h2>How probabilities are calculated</h2></div>
    <div className="model-card-grid">
      <section className="model-card"><span className="model-card-number">01</span><h3>{result?.model.name ?? "Interpretable Elo baseline"}</h3><p>Elo supplies the base map probability. Cutoff-safe imported context adjusts it before best-of-three or best-of-five probabilities are calculated under the stated same-map assumption.</p><span className="model-card-tag">{result?.model.status === "trained_chronological_split" ? "TRAINED ELO + CONTEXT PRIORS" : "BASELINE + CONTEXT PRIORS"}</span></section>
      <section className="model-card"><span className="model-card-number">02</span><h3>Cutoff-safe inputs</h3><p>Only completed matches played and available by the selected cutoff can update Elo or enter chronological training. Data imported later is not backfilled into an earlier snapshot.</p><span className="model-card-tag">CUTOFF {result ? formatTimestamp(result.cutoff) : "NOT RUN"}</span></section>
      <section className="model-card"><span className="model-card-number">03</span><h3>Monte Carlo progression</h3><p>Each simulation runs all three Swiss stages, direct entrants, and the playoffs in sequence. Advancement and title odds are frequencies across those complete paths.</p><span className="model-card-tag">{result?.simulations.toLocaleString() ?? "—"} RUNS · SEED {result?.seed ?? "—"}</span></section>
      <section className="model-card"><span className="model-card-number">04</span><h3>Imported match context</h3><p>Map rates use a Beta(4,4) prior; map-rate difference × 0.70 is capped at ±0.24 log-odds. Veto preference reweights maps by exp(0.7 × both teams' preference). Same-version rating and ADR differences use at least three active players per team; rating difference × 0.60 is capped at ±0.20, ADR × 0.002 at ±0.10. 90-day roster-continuity difference × 0.10 is capped at ±0.10; roster-adjusted H2H signal × 0.15 at ±0.15. The total context adjustment is capped at ±0.45. These are fixed heuristics, not learned effects.</p><span className="model-card-tag">MISSING OR INCOMPARABLE INPUTS ARE SKIPPED</span></section>
    </div>
    {result && result.model.metrics && Object.keys(result.model.metrics).length > 0 && <section className="panel evaluation-panel"><div className="panel-heading"><div><span className="section-overline">CHRONOLOGICAL HOLDOUT</span><h2>Evaluation metrics</h2><small>{result.model.metrics_scope ?? "Held-out Elo and format model, before context priors."}</small></div></div><div className="metric-row">{Object.entries(result.model.metrics).map(([key, value]) => <div key={key}><span>{key.replaceAll("_", " ")}</span><b>{value.toFixed(3)}</b></div>)}</div></section>}
  </div>;
}

function MatchDetailPanel(props: {
  match: PredictedMatch;
  detail: Record<string, unknown> | null;
  loading: boolean;
  teamMap: Map<string, Team>;
  probabilities: TournamentResult["probabilities"] | undefined;
  onClose: () => void;
  onManageResult: (match: PredictedMatch, action: ResultAction) => void;
}) {
  const { match, detail, loading, teamMap, probabilities, onClose, onManageResult } = props;
  const a = teamMap.get(match.team_a_id);
  const b = teamMap.get(match.team_b_id);
  const confirmed = match.result_status === "confirmed" || match.outcome_source === "completed_result";
  const prediction = confirmed && match.original_prediction ? match.original_prediction : match.prediction;
  const detailError = typeof detail?.detail_error === "string" ? detail.detail_error : null;
  const h2hCount = typeof detail?.head_to_head_count === "number" ? detail.head_to_head_count : 0;
  const recent = (detail?.recent_results ?? {}) as Record<string, { match_id: string; result: string; opponent_id: string; played_at: string; source: string }[]>;
  const headToHead = Array.isArray(detail?.head_to_head_results) ? detail.head_to_head_results as { match_id: string; played_at: string; winner_name: string; score?: string | null; best_of?: number | null }[] : [];
  const mapResults = Array.isArray(detail?.map_results) ? detail.map_results as { match_id: string; played_at: string; map_name: string; team_a_name: string; team_b_name: string; team_a_score?: number | null; team_b_score?: number | null }[] : [];
  const mapVetoes = Array.isArray(detail?.map_vetoes) ? detail.map_vetoes as { match_id: string; sequence_no: number; action: string; map_name: string; team_name: string; played_at: string }[] : [];
  const playerStats = Array.isArray(detail?.player_stats) ? detail.player_stats as { player_id: string; name: string; team_name: string; measurement_period: string; metric_version?: string | null; stats: Record<string, string | number | boolean | null> }[] : [];
  const contextFeatures = prediction.context_features ?? [];
  const errorProbability = (key: string) => probabilities?.[key]?.champion?.probability ?? 0;
  return <div className="detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="match-detail-panel" role="dialog" aria-modal="true" aria-labelledby="match-detail-title">
      <div className="detail-topline"><span className="section-overline">MATCH INTELLIGENCE · BO{match.best_of}</span><button className="detail-close" onClick={onClose} aria-label="Close match detail">×</button></div>
      <h2 id="match-detail-title">{a?.name ?? match.team_a_id} <i>vs</i> {b?.name ?? match.team_b_id}</h2>
      <div className={`detail-result-label${match.result_status === "confirmed" || match.outcome_source === "completed_result" ? " result-official" : ""}`}><i />{match.result_status === "confirmed" || match.outcome_source === "completed_result" ? `Confirmed winner: ${teamMap.get(match.confirmed_winner_id ?? match.winner_id ?? "")?.name ?? "Unknown"}${(match.confirmed_score ?? match.score) ? ` · ${match.confirmed_score ?? match.score}` : ""}` : match.fixture_confirmed ? "Confirmed fixture · result projected" : "Projected pairing · opponents depend on earlier rounds"}</div>
      <div className="detail-result-actions"><button onClick={() => onManageResult(match, match.result_status === "confirmed" || match.outcome_source === "completed_result" ? "edit" : "set")}>{match.result_status === "confirmed" || match.outcome_source === "completed_result" ? "Edit Result" : "Set Result"}</button>{(match.result_status === "confirmed" || match.outcome_source === "completed_result") && <button className="result-clear-button" onClick={() => onManageResult(match, "clear")}>Clear Result</button>}</div>
      {match.original_prediction && <div className="detail-result-label detail-original-pick"><i />Original model pick: {teamMap.get(match.original_prediction.predicted_winner_id)?.name ?? "Unknown"} · {percentage(match.original_prediction.team_a_probability)} / {percentage(match.original_prediction.team_b_probability)} before the result was entered</div>}
      <div className="detail-probability-pair">
        <div><TeamMark name={a?.name ?? "TBD"} /><b>{a?.name ?? "TBD"}</b><strong>{percentage(prediction.team_a_probability)}</strong><small>{match.original_prediction && confirmed ? "ORIGINAL MATCH PROBABILITY" : "MATCH WIN PROBABILITY"}</small></div>
        <div><TeamMark name={b?.name ?? "TBD"} /><b>{b?.name ?? "TBD"}</b><strong>{percentage(prediction.team_b_probability)}</strong><small>{match.original_prediction && confirmed ? "ORIGINAL MATCH PROBABILITY" : "MATCH WIN PROBABILITY"}</small></div>
      </div>
      <div className="probability-track detail-track"><i style={{ width: `${prediction.team_a_probability * 100}%` }} /></div>
      <div className="detail-explanation"><span className="section-overline">WHY THIS PICK?</span><p>{prediction.explanation}</p></div>
      <div className="detail-input-grid">
        <div><span>{a?.name ?? "Team A"} Elo</span><b>{prediction.rating_a.toFixed(0)}</b></div>
        <div><span>{b?.name ?? "Team B"} Elo</span><b>{prediction.rating_b.toFixed(0)}</b></div>
        <div><span>Baseline reliability</span><b className="reliability-low">{prediction.reliability}</b></div>
        <div><span>H2H matches available</span><b>{loading ? "…" : h2hCount}</b></div>
      </div>
      <div className="detail-subsection"><span className="section-overline">PROBABILITY INPUTS · LOG-ODDS ADJUSTMENTS</span>
        <div className="detail-record-list">{contextFeatures.map((item) => <div className="detail-record-row" key={item.key}><span>{item.label} · {item.sample_size} records<br />{item.summary}</span><b>{item.used ? `${item.log_odds_adjustment >= 0 ? "+" : ""}${item.log_odds_adjustment.toFixed(3)}` : "Not used"}</b></div>)}</div>
        <small className="detail-empty-data">{prediction.context_method ?? "Context adjustments are fixed heuristics, not trained coefficients."}</small>
      </div>
      <div className="detail-stage-odds"><span>OVERALL TOURNAMENT ODDS</span><div><b>{a?.name ?? "Team A"} reach title</b><strong>{percentage(errorProbability(match.team_a_id))}</strong></div><div><b>{b?.name ?? "Team B"} reach title</b><strong>{percentage(errorProbability(match.team_b_id))}</strong></div><small>These are unconditional title probabilities, not conditional odds for this matchup.</small></div>
      <div className="detail-subsection"><span className="section-overline">RECENT FORM AVAILABLE AT CUTOFF</span>
        {[match.team_a_id, match.team_b_id].map((teamId) => <div className="recent-form-row" key={teamId}><b>{teamMap.get(teamId)?.name ?? teamId}</b><span>{loading ? "Loading…" : (recent[teamId] ?? []).length ? recent[teamId].slice(-5).map((item) => <i key={item.match_id} className={item.result === "W" ? "form-win" : "form-loss"} title={`${item.result} · ${item.played_at}`}>{item.result}</i>) : <small>No eligible match history</small>}</span></div>)}
      </div>
      <div className="detail-subsection"><span className="section-overline">HEAD-TO-HEAD HISTORY · {h2hCount} MATCHES</span>
        {headToHead.length ? <div className="detail-record-list">{headToHead.slice().reverse().map((item) => <div className="detail-record-row" key={item.match_id}><span>{formatDate(item.played_at)} · BO{item.best_of ?? "?"}</span><b>{item.winner_name} won{item.score ? ` · ${item.score}` : ""}</b></div>)}</div> : <small className="detail-empty-data">{loading ? "Loading history…" : "No eligible head-to-head results at this cutoff."}</small>}
      </div>
      <div className="detail-subsection"><span className="section-overline">MAP RESULTS · {mapResults.length} AVAILABLE</span>
        {mapResults.length ? <div className="detail-record-list">{mapResults.slice(0, 8).map((item) => <div className="detail-record-row" key={`${item.match_id}:${item.map_name}`}><span>{item.map_name} · {formatDate(item.played_at)}</span><b>{item.team_a_name} {item.team_a_score ?? "—"}–{item.team_b_score ?? "—"} {item.team_b_name}</b></div>)}</div> : <small className="detail-empty-data">{loading ? "Loading map data…" : "No cutoff-eligible map results imported."}</small>}
      </div>
      <div className="detail-subsection"><span className="section-overline">HISTORICAL VETOES · {mapVetoes.length} AVAILABLE</span>
        {mapVetoes.length ? <div className="detail-record-list">{mapVetoes.slice(0, 8).map((item) => <div className="detail-record-row" key={`${item.match_id}:${item.sequence_no}`}><span>{item.team_name} · {item.action} · {formatDate(item.played_at)}</span><b>{item.map_name}</b></div>)}</div> : <small className="detail-empty-data">{loading ? "Loading veto history…" : "No cutoff-eligible veto history imported."}</small>}
      </div>
      <div className="detail-subsection"><span className="section-overline">PLAYER STATISTICS · {playerStats.length} RECORDS</span>
        {playerStats.length ? <div className="detail-record-list">{playerStats.slice(0, 8).map((item) => {
          const metrics = Object.entries(item.stats).filter(([, value]) => value === null || ["string", "number", "boolean"].includes(typeof value)).slice(0, 5);
          return <div className="detail-player-row" key={`${item.player_id}:${item.measurement_period}`}><div><b>{item.name}</b><span>{item.team_name} · {item.measurement_period}{item.metric_version ? ` · ${item.metric_version}` : ""}</span></div><small>{metrics.length ? metrics.map(([key, value]) => `${key.replaceAll("_", " ")} ${value ?? "—"}`).join(" · ") : "No numeric summary fields"}</small></div>;
        })}</div> : <small className="detail-empty-data">{loading ? "Loading player data…" : "No cutoff-eligible active-roster statistics imported."}</small>}
      </div>
      <div className="detail-subsection detail-limits"><span className="section-overline">DATA LIMITS</span><p>{detailError ?? (Array.isArray(prediction.warnings) && prediction.warnings.length ? prediction.warnings.join(" ") : "No warning was generated for this matchup.")}</p><div><span>Map pool and veto history</span><b>{prediction.map_veto_used ? "Veto reweighting active" : mapVetoes.length ? "Veto rows present, no map-weight change" : prediction.map_pool_used ? "Map results active" : "No eligible records"}</b></div><div><span>Player ratings / roster continuity</span><b>{prediction.player_metrics_used || prediction.roster_continuity_used ? "Available signals active" : "No comparable snapshot"}</b></div><div><span>Head-to-head signal</span><b>{prediction.head_to_head_used ? "Used with recency / roster shrinkage" : "No prior meetings"}</b></div></div>
    </aside>
  </div>;
}
