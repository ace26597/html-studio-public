import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { whenAccessDenied } from "./api";
import { goToSignIn } from "./signIn";
import "./styles.css";

// Any call answered 401 `access_denied` (an expired cookie, a changed key) opens the sign-in page.
whenAccessDenied(goToSignIn);

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
