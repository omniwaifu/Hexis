import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Home from "./page";

describe("Home", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          status: { stage: "not_started" },
          profile: {},
          mode: "persona",
        }),
      })) as unknown as typeof fetch
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the model selection stage", async () => {
    render(<Home />);

    expect(screen.getByText("Initialization Ritual")).toBeInTheDocument();
    expect(
      await screen.findByText(/Select the conscious and subconscious models/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/Stage 1 of/i)).toBeInTheDocument();
  });
});
