import type { PredictedMatch, ProjectedStage, Team } from "@/lib/types";
import { percentage } from "@/lib/helpers";

type RecordKey = `${number}-${number}`;

type SwissMatch = {
  match: PredictedMatch;
  record: RecordKey;
};

function outcomeWinner(match: PredictedMatch) {
  const confirmed = match.result_status === "confirmed" || match.outcome_source === "completed_result";
  return confirmed ? (match.confirmed_winner_id ?? match.winner_id) : match.winner_id;
}

function TeamLine({ team, probability, winner, actual }: { team: Team | undefined; probability: number; winner: boolean; actual: boolean }) {
  if (!team) return <div className="swiss-team swiss-team-tbd"><span>—</span><small>TBD</small></div>;
  return <div className={`swiss-team${winner ? " swiss-team-winner" : ""}${actual && winner ? " swiss-team-actual" : ""}`}>
    <span className="swiss-team-mark">{team.name.slice(0, 2).toUpperCase()}</span>
    <b>{team.name}</b>
    <small>{percentage(probability)}</small>
  </div>;
}

function SwissMatchCard({ entry, teamMap }: { entry: SwissMatch; teamMap: Map<string, Team> }) {
  const { match, record } = entry;
  const confirmed = match.result_status === "confirmed" || match.outcome_source === "completed_result";
  const winnerId = outcomeWinner(match);
  const prediction = match.original_prediction ?? match.prediction;
  const modelWinnerId = prediction.predicted_winner_id;
  const score = confirmed ? (match.confirmed_score ?? match.score) : null;
  const a = teamMap.get(match.team_a_id);
  const b = teamMap.get(match.team_b_id);

  return <article className={`swiss-match${confirmed ? " swiss-match-confirmed" : ""}`}>
    <div className="swiss-match-top"><span>{record}</span><small>BO{match.best_of}</small></div>
    <TeamLine team={a} probability={prediction.team_a_probability} winner={winnerId === match.team_a_id} actual={confirmed} />
    <div className="swiss-vs">{score ? score.replace(":", "–") : "VS"}</div>
    <TeamLine team={b} probability={prediction.team_b_probability} winner={winnerId === match.team_b_id} actual={confirmed} />
    <div className="swiss-pick">
      <span>{confirmed ? "MODEL PICK" : "PREDICTED WINNER"}</span>
      <b>{teamMap.get(modelWinnerId)?.name ?? "TBD"}</b>
      {confirmed && <em className={winnerId === modelWinnerId ? "" : "swiss-pick-missed"}>{winnerId === modelWinnerId ? "CORRECT" : "MISSED"}</em>}
    </div>
  </article>;
}

function recordGroups(round: ProjectedStage["rounds"][number], previous: Map<string, { wins: number; losses: number }>) {
  const entries: SwissMatch[] = round.matches.map((match) => {
    const record = previous.get(match.team_a_id) ?? { wins: 0, losses: 0 };
    return { match, record: `${record.wins}-${record.losses}` as RecordKey };
  });
  const groups = new Map<RecordKey, SwissMatch[]>();
  entries.forEach((entry) => groups.set(entry.record, [...(groups.get(entry.record) ?? []), entry]));
  return groups;
}

function updateRecords(round: ProjectedStage["rounds"][number], records: Map<string, { wins: number; losses: number }>) {
  round.matches.forEach((match) => {
    const winner = outcomeWinner(match);
    const loser = winner === match.team_a_id ? match.team_b_id : match.team_a_id;
    const winnerRecord = records.get(winner) ?? { wins: 0, losses: 0 };
    const loserRecord = records.get(loser) ?? { wins: 0, losses: 0 };
    records.set(winner, { ...winnerRecord, wins: winnerRecord.wins + 1 });
    records.set(loser, { ...loserRecord, losses: loserRecord.losses + 1 });
  });
}

/** A stage-wide visual summary. Swiss pairings are grouped by the teams' record entering each round. */
export function SwissOverview({ stage, teamMap }: { stage: ProjectedStage; teamMap: Map<string, Team> }) {
  const records = new Map<string, { wins: number; losses: number }>();
  const columns = stage.rounds.map((round) => {
    const groups = recordGroups(round, records);
    updateRecords(round, records);
    return { round, groups };
  });

  return <section className="swiss-overview" aria-label="Swiss stage path overview">
    <div className="swiss-overview-heading">
      <div><span className="section-overline">STAGE AT A GLANCE</span><h2>Swiss path overview</h2></div>
      <div className="swiss-legend"><span><i className="swiss-legend-predicted" /> Predicted winner</span><span><i className="swiss-legend-confirmed" /> Actual winner</span><span>Win rate shown beside each team</span></div>
    </div>
    <p className="swiss-overview-copy">Each column is one round. Cards are grouped by the record a team held entering that round, so later pairings remain conditional on earlier results.</p>
    <div className="swiss-board-wrap">
      <div className="swiss-board">
        {columns.map(({ round, groups }, index) => <div className="swiss-column" key={round.round_no}>
          <div className="swiss-column-heading"><span>ROUND {round.round_no}</span><b>{round.name ?? `Swiss round ${round.round_no}`}</b></div>
          {[...groups.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([record, entries]) => <section className="swiss-record-group" key={record}>
            <div className={`swiss-record-label swiss-record-${record.replace("-", "-")}`}><b>{record}</b><span>{record === "3-0" || record === "3-1" || record === "3-2" ? "ADVANCE PATH" : record === "0-3" || record === "1-3" || record === "2-3" ? "ELIMINATION PATH" : "IN PLAY"}</span></div>
            <div className="swiss-match-list">{entries.map((entry) => <SwissMatchCard key={entry.match.id} entry={entry} teamMap={teamMap} />)}</div>
          </section>)}
          {index < columns.length - 1 && <div className="swiss-flow-arrow" aria-hidden="true">→</div>}
        </div>)}
      </div>
    </div>
    <div className="swiss-overview-note"><span>ⓘ</span><span><b>Transparency means a model call.</b> Confirmed outcomes have full contrast and retain the original model prediction for comparison.</span></div>
  </section>;
}
