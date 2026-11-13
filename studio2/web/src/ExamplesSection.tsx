// Examples that come with the studio: finished pages a new install can open as a copy.

import { exampleFacts, KIND_LABEL, type Example } from "./examples";
import { Spinner } from "./icons";

export function ExamplesSection({
  examples,
  opening,
  onOpen,
}: {
  examples: Example[];
  /** The id of the example being copied, while that is happening. */
  opening: string | null;
  onOpen: (example: Example) => void;
}) {
  if (examples.length === 0) return null;
  return (
    <section className="examples" aria-labelledby="examples-title">
      <div className="examples-head">
        <h2 id="examples-title">Examples</h2>
        <p>Made with this studio from a request or two. A copy is yours to read, change or draw on.</p>
      </div>
      <ul className="examples-grid">
        {examples.map((example) => {
          const facts = exampleFacts(example);
          return (
            <li key={example.id} className="example">
              <div className="example-shot">
                {example.preview && <img src={example.preview} alt="" loading="lazy" />}
                <span className="example-kind">{KIND_LABEL[example.kind]}</span>
              </div>
              <div className="example-text">
                <h3>{example.title}</h3>
                <p className="example-summary">{example.summary}</p>
                {facts && <p className="example-facts">{facts}</p>}
              </div>
              <button
                type="button"
                className="btn example-open"
                disabled={opening !== null}
                onClick={() => onOpen(example)}
              >
                {opening === example.id ? <Spinner size={14} /> : null}
                Open a copy
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
