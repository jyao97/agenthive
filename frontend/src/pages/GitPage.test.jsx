import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import GitPage from "./GitPage";

// --- Mock navigate ---
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

// --- Mock API ---
vi.mock("../lib/api", () => ({
  fetchProjects: vi.fn(),
  fetchGitLog: vi.fn(),
  fetchGitBranches: vi.fn(),
  fetchGitStatus: vi.fn(),
  fetchGitWorktrees: vi.fn(),
  createAgent: vi.fn(),
}));

import {
  fetchProjects,
  fetchGitLog,
  fetchGitBranches,
  fetchGitStatus,
  fetchGitWorktrees,
  createAgent,
} from "../lib/api";

// --- Helpers ---
function renderGitPage() {
  return render(
    <MemoryRouter>
      <GitPage theme="dark" onToggleTheme={() => {}} />
    </MemoryRouter>
  );
}

const MOCK_PROJECTS = [
  { name: "my-project", display_name: "My Project" },
  { name: "other", display_name: "Other" },
];

const MOCK_COMMITS = [
  { hash: "abc1234567", message: "Initial commit", author: "dev", date: new Date().toISOString() },
];

const MOCK_BRANCHES = [
  { name: "main", current: true },
  { name: "feature-x", current: false },
];

const MOCK_STATUS_CLEAN = { branch: "main", clean: true, staged: [], unstaged: [], untracked: [] };

const MOCK_WORKTREES_SINGLE = [
  { path: "/home/user/project", branch: "main", commit: "abc1234", detached: false },
];

const MOCK_WORKTREES_MULTI = [
  { path: "/home/user/project", branch: "main", commit: "abc1234", detached: false },
  { path: "/home/user/.claude/worktrees/feat-a", branch: "feat-a", commit: "def5678", detached: false },
  { path: "/home/user/.claude/worktrees/feat-b", branch: "feat-b", commit: "ghi9012", detached: false },
];

function setupMocks({ worktrees = MOCK_WORKTREES_SINGLE } = {}) {
  fetchProjects.mockResolvedValue(MOCK_PROJECTS);
  fetchGitLog.mockResolvedValue(MOCK_COMMITS);
  fetchGitBranches.mockResolvedValue(MOCK_BRANCHES);
  fetchGitStatus.mockResolvedValue(MOCK_STATUS_CLEAN);
  fetchGitWorktrees.mockResolvedValue(worktrees);
  createAgent.mockResolvedValue({ id: "agent-123" });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockNavigate.mockClear();
});

// =============================================================================
// Section ordering
// =============================================================================
describe("GitPage section ordering", () => {
  it("renders Worktrees section before Branches section", async () => {
    setupMocks({ worktrees: MOCK_WORKTREES_MULTI });
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("Worktrees")).toBeInTheDocument();
      expect(screen.getByText("Branches")).toBeInTheDocument();
    });

    // Get all section headings in order
    const headings = screen.getAllByRole("heading", { level: 2 });
    const headingTexts = headings.map((h) => h.textContent.trim());

    const worktreeIdx = headingTexts.indexOf("Worktrees");
    const branchIdx = headingTexts.indexOf("Branches");
    expect(worktreeIdx).toBeLessThan(branchIdx);
  });

  it("renders sections in order: Worktrees, Branches, Status, Commit Log", async () => {
    setupMocks({ worktrees: MOCK_WORKTREES_MULTI });
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("Commit Log")).toBeInTheDocument();
    });

    const headings = screen.getAllByRole("heading", { level: 2 });
    const headingTexts = headings.map((h) => h.textContent.trim());

    expect(headingTexts).toEqual(["Worktrees", "Branches", "Status", "Commit Log"]);
  });
});

// =============================================================================
// Merge All button visibility
// =============================================================================
describe("Merge All button visibility", () => {
  it("does NOT show Merge All when only main worktree exists", async () => {
    setupMocks({ worktrees: MOCK_WORKTREES_SINGLE });
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("Worktrees")).toBeInTheDocument();
    });

    expect(screen.queryByText("Merge All")).not.toBeInTheDocument();
  });

  it("shows Merge All when multiple worktrees exist", async () => {
    setupMocks({ worktrees: MOCK_WORKTREES_MULTI });
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("Merge All")).toBeInTheDocument();
    });
  });

  it("does NOT show Merge All when worktrees array is empty", async () => {
    setupMocks({ worktrees: [] });
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("Worktrees")).toBeInTheDocument();
    });

    expect(screen.queryByText("Merge All")).not.toBeInTheDocument();
  });
});

// =============================================================================
// Merge All button behavior
// =============================================================================
describe("Merge All button behavior", () => {
  it("calls createAgent with correct params on click", async () => {
    setupMocks({ worktrees: MOCK_WORKTREES_MULTI });
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("Merge All")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Merge All"));

    await waitFor(() => {
      expect(createAgent).toHaveBeenCalledTimes(1);
    });

    const call = createAgent.mock.calls[0][0];
    expect(call.project).toBe("my-project");
    expect(call.mode).toBe("AUTO");
    expect(call.skip_permissions).toBe(true);
    expect(call.prompt).toContain("feat-a");
    expect(call.prompt).toContain("feat-b");
  });

  it("navigates to agent chat page after creating agent", async () => {
    setupMocks({ worktrees: MOCK_WORKTREES_MULTI });
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("Merge All")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Merge All"));

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/agents/agent-123");
    });
  });

  it("shows Creating... text while agent is being created", async () => {
    let resolveCreate;
    createAgent.mockReturnValue(new Promise((r) => { resolveCreate = r; }));
    setupMocks({ worktrees: MOCK_WORKTREES_MULTI });
    // Override the createAgent mock set by setupMocks
    createAgent.mockReturnValue(new Promise((r) => { resolveCreate = r; }));

    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("Merge All")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Merge All"));

    await waitFor(() => {
      expect(screen.getByText("Creating...")).toBeInTheDocument();
    });

    // Button should be disabled during loading
    const btn = screen.getByText("Creating...").closest("button");
    expect(btn).toBeDisabled();

    // Resolve and verify it returns to normal
    resolveCreate({ id: "agent-456" });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/agents/agent-456");
    });
  });

  it("shows error toast when createAgent fails", async () => {
    setupMocks({ worktrees: MOCK_WORKTREES_MULTI });
    createAgent.mockRejectedValue(new Error("Server error"));

    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("Merge All")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Merge All"));

    await waitFor(() => {
      expect(screen.getByText("Merge All error: Server error")).toBeInTheDocument();
    });

    // Should NOT navigate
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("does not include main branch in the merge instruction", async () => {
    setupMocks({ worktrees: MOCK_WORKTREES_MULTI });
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("Merge All")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Merge All"));

    await waitFor(() => {
      expect(createAgent).toHaveBeenCalledTimes(1);
    });

    const msg = createAgent.mock.calls[0][0].prompt;
    // The branch list should be "feat-a, feat-b" — not include "main"
    expect(msg).toMatch(/branches to merge are: feat-a, feat-b/);
  });
});

// =============================================================================
// Worktree rendering
// =============================================================================
describe("Worktree rendering", () => {
  it("shows 'No additional worktrees' when only main exists", async () => {
    setupMocks({ worktrees: MOCK_WORKTREES_SINGLE });
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("No additional worktrees.")).toBeInTheDocument();
    });
  });

  it("renders all worktrees with names and branches", async () => {
    setupMocks({ worktrees: MOCK_WORKTREES_MULTI });
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("project")).toBeInTheDocument();
      // feat-a appears twice: once as folder name, once as branch badge
      expect(screen.getAllByText("feat-a").length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText("feat-b").length).toBeGreaterThanOrEqual(1);
    });
  });

  it("shows detached badge for detached worktrees", async () => {
    const worktrees = [
      { path: "/home/user/project", branch: "main", commit: "abc", detached: false },
      { path: "/home/user/.claude/worktrees/fix", branch: null, commit: "xyz", detached: true },
    ];
    setupMocks({ worktrees });
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("detached")).toBeInTheDocument();
    });
  });
});

// =============================================================================
// Branch merge (spawns agent)
// =============================================================================
describe("Branch merge", () => {
  it("spawns an agent to merge branch on button click", async () => {
    setupMocks();
    renderGitPage();

    await waitFor(() => {
      expect(screen.getByText("feature-x")).toBeInTheDocument();
    });

    // The Merge button next to feature-x
    const mergeBtn = screen.getAllByText("Merge").find(
      (el) => el.tagName === "BUTTON"
    );
    fireEvent.click(mergeBtn);

    await waitFor(() => {
      expect(createAgent).toHaveBeenCalledTimes(1);
    });

    const call = createAgent.mock.calls[0][0];
    expect(call.project).toBe("my-project");
    expect(call.mode).toBe("AUTO");
    expect(call.prompt).toContain("feature-x");
    expect(mockNavigate).toHaveBeenCalledWith("/agents/agent-123");
  });
});

// =============================================================================
// Project loading
// =============================================================================
describe("Project loading", () => {
  it("shows no-projects message when empty", async () => {
    fetchProjects.mockResolvedValue([]);
    renderGitPage();

    await waitFor(() => {
      expect(
        screen.getByText("No projects registered. Add a project to view its git history.")
      ).toBeInTheDocument();
    });
  });

  it("selects first project by default and fetches git data", async () => {
    setupMocks();
    renderGitPage();

    await waitFor(() => {
      expect(fetchGitLog).toHaveBeenCalledWith("my-project");
      expect(fetchGitBranches).toHaveBeenCalledWith("my-project");
      expect(fetchGitStatus).toHaveBeenCalledWith("my-project");
      expect(fetchGitWorktrees).toHaveBeenCalledWith("my-project");
    });
  });
});
