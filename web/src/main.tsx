import "@cloudscape-design/global-styles/index.css";
import "./styles.css";

import { applyDensity, applyMode, Density, Mode } from "@cloudscape-design/global-styles";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";

applyMode(Mode.Dark);
applyDensity(Density.Comfortable);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
