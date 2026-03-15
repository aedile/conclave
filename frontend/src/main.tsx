/**
 * main.tsx — React application entry point.
 *
 * Mounts the React application into the #root DOM element.
 * Uses StrictMode for development-time safety checks.
 * Wraps App in BrowserRouter for React Router v6 routing.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles/global.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error(
    "Root element #root not found. Ensure index.html contains <div id='root'></div>.",
  );
}

createRoot(rootElement).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
