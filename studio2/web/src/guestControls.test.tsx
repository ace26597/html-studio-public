// A guest on a shared studio is not offered what only its owner may do (CONTRACT.md, "What a guest
// may not do on a shared studio").
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { NewProjectDialog } from "./NewProjectDialog";

const noop = () => undefined;

describe("New page for a guest", () => {
  it("offers no app on this computer when there is no way to bring one in", () => {
    const guest = renderToStaticMarkup(<NewProjectDialog creating={false} createError={null} onCreate={noop} onClose={noop} />);
    expect(guest).not.toContain("An app on this computer");
    const owner = renderToStaticMarkup(
      <NewProjectDialog creating={false} createError={null} onCreate={noop} onAttach={noop} onClose={noop} />,
    );
    expect(owner).toContain("An app on this computer");
  });
});
