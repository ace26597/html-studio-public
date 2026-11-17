import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Modal } from "./Modal";
import { NewProjectDialog } from "./NewProjectDialog";

// A dialog that fills a phone or iPad screen has no backdrop left to tap, and there may be no
// Escape key: every dialog needs a close control a finger can reach.
const closeButton = /<button[^>]*class="[^"]*dialog-close[^"]*"[^>]*>/;

describe("a dialog can be closed by touch alone", () => {
  it("has a labelled close button in its header, before its content", () => {
    const html = renderToStaticMarkup(
      <Modal title="Make it live" onClose={() => undefined} wide>
        <p id="content">Body</p>
      </Modal>,
    );
    const button = html.match(closeButton)?.[0];
    expect(button).toBeTruthy();
    expect(button).toContain('aria-label="Close"');
    expect(button).toContain('type="button"');
    expect(button).not.toContain("disabled");
    expect(html.indexOf("dialog-close")).toBeLessThan(html.indexOf('id="content"'));
  });

  it("can take focus itself, for a dialog that has nothing else to focus yet", () => {
    const html = renderToStaticMarkup(
      <Modal title="Export" onClose={() => undefined}>
        <p>Loading</p>
      </Modal>,
    );
    expect(html).toMatch(/<div[^>]*role="dialog"[^>]*tabindex="-1"/);
  });

  it("is disabled while a request is in flight, as Escape and the backdrop are", () => {
    const html = renderToStaticMarkup(
      <Modal title="Save as template" onClose={() => undefined} busy>
        <p>Body</p>
      </Modal>,
    );
    expect(html.match(closeButton)?.[0]).toContain("disabled");
  });

  it("is on the new page dialog too", () => {
    const html = renderToStaticMarkup(
      <NewProjectDialog creating={false} createError={null} onCreate={() => undefined} onAttach={() => undefined} onClose={() => undefined} />,
    );
    expect(html.match(closeButton)?.[0]).toContain('aria-label="Close"');
  });
});
