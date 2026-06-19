import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import Login from "./Login.jsx";
import "./styles.css";

const AUTH_KEY = "oe_authed";

// Gate the whole app behind the passcode login. The unlocked flag persists in
// localStorage so a refresh stays signed in until the user logs out.
function Root() {
  const [authed, setAuthed] = useState(() => localStorage.getItem(AUTH_KEY) === "1");

  function onAuthed() {
    localStorage.setItem(AUTH_KEY, "1");
    setAuthed(true);
  }
  function onLogout() {
    localStorage.removeItem(AUTH_KEY);
    setAuthed(false);
  }

  return authed ? <App onLogout={onLogout} /> : <Login onAuthed={onAuthed} />;
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
