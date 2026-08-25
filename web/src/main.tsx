import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";

const container = document.getElementById("root");
if (container === null) throw new Error("#root is missing from the document");

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
