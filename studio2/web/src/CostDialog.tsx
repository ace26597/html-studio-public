// What this page's requests cost: charged, plan-equivalent and unpriced, kept apart.

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { canRecover, costRows, pendingNote, recoveryMessage, turnAmount, turnTip } from "./cost";
import { Spinner } from "./icons";
import { Modal } from "./Modal";
import type { ProjectCost } from "./types";
import { useToast } from "./ui";

interface CostDialogProps {
  projectId: string;
  /** A turn is running: a lookup in the gateway's log waits for it. */
  running: boolean;
  onClose: () => void;
}

export function CostDialog({ projectId, running, onClose }: CostDialogProps) {
  const toast = useToast();
  const [cost, setCost] = useState<ProjectCost | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [looking, setLooking] = useState(false);

  const load = useCallback(
    (signal?: AbortSignal) =>
      api.getCost(projectId, signal).then(
        (next) => {
          setCost(next);
          setError(null);
        },
        (failure) => {
          if (!signal?.aborted) setError(errorMessage(failure));
        },
      ),
    [projectId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const lookUp = async () => {
    setLooking(true);
    try {
      const result = await api.recoverCost(projectId);
      toast({ message: recoveryMessage(result), kind: result.recovered > 0 ? "success" : "info", duration: 8000 });
      await load();
    } catch (failure) {
      toast({ message: errorMessage(failure), kind: "error" });
    } finally {
      setLooking(false);
    }
  };

  const pending = cost ? pendingNote(cost) : null;
  return (
    <Modal title="What the requests cost" onClose={onClose} wide>
      {error && <p className="dialog-body" role="alert">{error}</p>}
      {!cost && !error && (
        <p className="dialog-body">
          <Spinner /> Adding up…
        </p>
      )}
      {cost && (
        <>
          <dl className="cost-rows">
            {costRows(cost).map((row) => (
              <div key={row.key} className={`cost-row is-${row.key}`}>
                <dt>{row.label}</dt>
                <dd>{row.value}</dd>
                <dd className="cost-note">{row.note}</dd>
              </div>
            ))}
          </dl>
          {pending && <p className="dialog-body">{pending}</p>}
          {cost.turns.length === 0 ? (
            <p className="dialog-body">No request in this page has used a model yet.</p>
          ) : (
            <details className="cost-turns">
              <summary>
                {cost.turns.length} {cost.turns.length === 1 ? "turn" : "turns"}
              </summary>
              <ul>
                {[...cost.turns].reverse().map((turn) => (
                  <li key={turn.turn_id} data-kind={turn.kind}>
                    <span className="cost-when">{new Date(turn.at).toLocaleString()}</span>
                    <span className="cost-model">{turn.model ?? turn.connection ?? ""}</span>
                    <span className="cost-amount" title={turnTip(turn) ?? undefined}>
                      {turnAmount(turn)}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
      <div className="dialog-actions">
        {cost && canRecover(cost) && (
          <button type="button" className="btn" disabled={looking || running} onClick={lookUp}
            title={running ? "Wait for the running turn to end" : "Match turns from before tagging by session and time"}>
            {looking ? "Looking…" : "Look unpriced turns up in the gateway's log"}
          </button>
        )}
        <button type="button" className="btn btn-primary" onClick={onClose} data-autofocus>
          Close
        </button>
      </div>
    </Modal>
  );
}
