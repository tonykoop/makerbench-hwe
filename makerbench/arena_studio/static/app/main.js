import { render } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { html } from "./html.js";
import { StudioHeader } from "./components/header.js";
import { useResource } from "./hooks/useResource.js";
import { loadVoter, normalizeVoter, saveVoter } from "./lib/prefs.js";
import { buildHash, parseHash } from "./lib/route.js";
import { LaunchScreen } from "./screens/launch.js";
import { AnalyticsScreen } from "./screens/analytics.js";
import { CompareScreen } from "./screens/compare.js";
import { RunsScreen } from "./screens/runs.js";
import { VoteScreen } from "./screens/vote.js";

// Screens appear in the rail only once they exist. `takesRun` screens keep the
// header's run choice in their route (#/<screen>/<run_id>).
export const SCREENS = [
  { id: "runs", label: "Runs", component: RunsScreen, takesRun: true },
  // `blind` screens never request anything that names entrants before a vote.
  { id: "vote", label: "Blind voting", component: VoteScreen, takesRun: true, blind: true },
  { id: "launch", label: "Launch", component: LaunchScreen },
  { id: "analytics", label: "Agreement analytics", component: AnalyticsScreen, takesRun: true },
  { id: "compare", label: "Compare runs", component: CompareScreen },
];

function useRoute() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onHashChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  return route;
}

function UnknownScreen({ route }) {
  return html`
    <div class="screen">
      <h1 tabindex="-1">Screen not found</h1>
      <p class="lede">
        There's no Studio screen called “${route.screen}”. <a href="#/runs">Go to Runs</a>.
      </p>
    </div>
  `;
}

function App() {
  const route = useRoute();
  const [voter, setVoter] = useState(loadVoter);
  const screen = SCREENS.find((candidate) => candidate.id === route.screen) || null;
  // /api/runs lists each run's entrants, so blind screens don't load it.
  const runs = useResource(screen?.blind ? null : "/api/runs");
  const mainRef = useRef(null);
  const firstScreen = useRef(true);

  useEffect(() => {
    document.title = screen ? `${screen.label}: Arena Studio` : "Arena Studio";
    if (firstScreen.current) {
      firstScreen.current = false;
      return;
    }
    // Moving between screens puts keyboard focus on the new screen's heading.
    mainRef.current?.querySelector("h1")?.focus();
  }, [route.screen]);

  const runId = screen?.takesRun ? route.args[0] || null : null;

  const selectRun = (nextRunId) => {
    const target = screen?.takesRun ? screen.id : "runs";
    window.location.hash = buildHash(target, nextRunId ? [nextRunId] : []);
  };

  const changeVoter = (value) => {
    const next = normalizeVoter(value);
    setVoter(next);
    saveVoter(next);
  };

  const Screen = screen ? screen.component : UnknownScreen;

  return html`
    <a
      class="skip-link"
      href="#/"
      onClick=${(event) => {
        event.preventDefault();
        mainRef.current?.focus();
      }}
      >Skip to content</a
    >
    <div class="studio">
      <${StudioHeader}
        runs=${runs}
        blind=${Boolean(screen?.blind)}
        runId=${runId}
        onSelectRun=${selectRun}
        voter=${voter}
        onVoterChange=${changeVoter}
      />
      <nav class="rail" aria-label="Studio screens">
        <ul>
          ${SCREENS.map(
            (item) => html`
              <li key=${item.id}>
                <a
                  href=${buildHash(item.id, item.takesRun && runId ? [runId] : [])}
                  aria-current=${item.id === route.screen ? "page" : undefined}
                  >${item.label}</a
                >
              </li>
            `,
          )}
        </ul>
      </nav>
      <main id="main" tabindex="-1" ref=${mainRef}>
        <${Screen} route=${route} runs=${runs} voter=${voter} />
      </main>
    </div>
  `;
}

const root = document.getElementById("app");
root.textContent = "";
render(html`<${App} />`, root);
