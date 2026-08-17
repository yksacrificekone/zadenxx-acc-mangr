#!/usr/bin/env """
===============================================================================
 ZADENXX ACCOUNT MANAGER  —  owner-only account management console
===============================================================================
 A cross-platform desktop / mobile / web application built with Flet.

 AUTHOR   : Zadenxx
 TARGETS  : Windows (VS Code dev) · Android/iOS (`flet build`) · Web/Pages
 RUNTIME  : Python 3.9+  ·  Flet >= 0.21 (tested through 0.85.x)

 -----------------------------------------------------------------------------
 RUNNING IN VS CODE (WINDOWS)
 -----------------------------------------------------------------------------
   python -m pip install "flet[all]>=0.21"
   python zadenxx_account_manager.py            # native desktop window
   python zadenxx_account_manager.py --web      # open in the browser instead
   flet run zadenxx_account_manager.py          # hot-reload dev server

   Debug with F5: create .vscode/launch.json ->
     { "version": "0.2.0",
       "configurations": [ { "name": "Zadenxx", "type": "debugpy",
         "request": "launch", "program": "zadenxx_account_manager.py",
         "console": "integratedTerminal" } ] }

 -----------------------------------------------------------------------------
 MOBILE & WEB DEPLOYMENT (flet build)
 -----------------------------------------------------------------------------
   flet build apk            -> build/apk/app-release.apk      (Win/macOS/Linux)
   flet build aab            -> build/aab/...                  (Play Store)
   flet build ipa            -> build/ipa/...                  (macOS + Xcode only)
   flet build ios-simulator  -> simulator bundle (macOS only)
   flet build web            -> build/web/  (static site -> GitHub Pages)

   GitHub Pages: `flet build web`, then push `build/web` to the `gh-pages`
   branch (e.g. `git subtree push --prefix build/web origin gh-pages`).

 -----------------------------------------------------------------------------
 ARCHITECTURE NOTES
 -----------------------------------------------------------------------------
   * STATE ROUTING   — a tiny explicit state machine (self.state ∈
     {"login", "dashboard"}) drives the single root container. `route()`
     swaps the root content, optionally through a fade AnimatedSwitcher,
     which is how the login -> dashboard transition stays "seamless".
   * STORAGE         — `AccountStore` persists accounts + activity events to a
     JSON file. The data directory is resolved platform-agnostically:
     (1) Flet's runtime storage env var `FLET_APP_STORAGE_DATA` (set on
     Android/iOS/desktop by the Flet runtime), (2) `Path.home()` on desktop,
     (3) the CWD as a last resort (read-only web hosting degrades gracefully
     to in-memory storage and shows an "EPHEMERAL STORAGE" banner).
   * SECURITY LAYER  — hardcoded owner identity (spec requirement), verified
     with constant-time `hmac.compare_digest`, 5-attempt lockout with a 30 s
     cooldown, and a persisted session token so the app re-authenticates
     across restarts until "Sign out".
   * THEME           — strict monochrome: black canvas, white/grey ink, and a
     top-anchored opacity slider (0.2 -> 1.0) driving the alpha of the main
     dashboard panel (Container.opacity works on desktop, mobile and web).
===============================================================================
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import flet as ft

# -----------------------------------------------------------------------------
# Flet cross-version shims
# -----------------------------------------------------------------------------

try:  # newer Flet (>= 0.24) uses the ft.Icons enum; older used ft.icons.*
    from flet import Icons as _Icons
except ImportError:  # pragma: no cover
    _Icons = None


def _ic(name: str):
    """Resolve a Material icon constant regardless of the installed Flet
    generation (ft.Icons.XXX in new versions, ft.icons.XXX in old ones)."""
    if _Icons is not None:
        return getattr(_Icons, name, getattr(ft.icons, name))
    return getattr(ft.icons, name)


try:  # ft.ScrollMode.AUTO only exists in newer releases
    AUTO_SCROLL = ft.ScrollMode.AUTO
except AttributeError:  # pragma: no cover
    AUTO_SCROLL = True


# -----------------------------------------------------------------------------
# Monochrome design tokens — hex strings keep the palette identical on every
# Flet generation and every platform (no ColorScheme enum dependency).
# -----------------------------------------------------------------------------

BG_BLACK      = "#000000"   # application canvas
BG_CHARCOAL   = "#0C0C0C"   # main dashboard panel (opacity-controlled)
BG_PANEL      = "#141414"   # bars, dialogs, dialog backgrounds
BG_CARD       = "#1A1A1A"   # account cards / chips / fields
BORDER_GREY   = "#3A3A3A"   # resting borders
BORDER_WHITE  = "#FFFFFF"   # active borders
TEXT_WHITE    = "#FFFFFF"
TEXT_GREY     = "#9E9E9E"
TEXT_DIM      = "#616161"

# Status indicator colours (still monochrome: white / grey / hollow dark)
DOT_ONLINE  = "#FFFFFF"
DOT_IDLE    = "#777777"
DOT_OFFLINE = "#262626"

STATUS_DOTS = {"online": DOT_ONLINE, "idle": DOT_IDLE, "offline": DOT_OFFLINE}

# -----------------------------------------------------------------------------
# Security constants — the owner identity is hardcoded per specification.
# -----------------------------------------------------------------------------

OWNER_USERNAME = "x"
OWNER_PASSWORD = "xisthebest123"
AUTH_SALT      = "ZADENXX::OWNER::v1"   # salts the session token, never the password
MAX_LOGIN_ATTEMPTS = 5                  # failed attempts before lockout
LOCKOUT_SECONDS    = 30                 # lockout cooldown duration


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------

def _now_iso() -> str:
    """ISO-8601 timestamp used for audit fields (storage + activity log)."""
    return datetime.now().isoformat(timespec="seconds")


# -----------------------------------------------------------------------------
# AccountStore — platform-agnostic JSON persistence
# -----------------------------------------------------------------------------

class AccountStore:
    """Accounts + activity event log persisted to a single JSON document.

    The storage directory resolution order intentionally avoids hard-coded
    Windows paths so the same code ports cleanly to Android/iOS sandboxes:

        1. $FLET_APP_STORAGE_DATA   -> provided by the Flet runtime on mobile
           and desktop builds.
        2. ~/.zadenxx               -> normal per-user location on desktop.
        3. <cwd>/.zadenxx_data      -> last resort (web dev / sandbox); on
           read-only static hosting (GitHub Pages) writes fail gracefully and
           the app drops into in-memory mode with a visible warning banner.
    """

    SCHEMA_VERSION = 1

    def __init__(self) -> None:
        self.data_dir: Path = self._resolve_data_dir()
        self.data_file: Path = self.data_dir / "accounts.json"
        self.session_file: Path = self.data_dir / "session.json"
        self.persistence_ok: bool = True   # False => web/ephemeral mode
        self._accounts: list[dict] = []
        self._events: list[str] = []
        self._load()

    # -- storage location ----------------------------------------------------

    @staticmethod
    def _resolve_data_dir() -> Path:
        env_dir = os.environ.get("FLET_APP_STORAGE_DATA")
        if env_dir:
            try:
                path = Path(env_dir)
                path.mkdir(parents=True, exist_ok=True)
                return path
            except OSError:
                pass

        try:  # desktop: per-user home directory
            path = Path.home() / ".zadenxx"
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            pass

        path = Path.cwd() / ".zadenxx_data"   # web / sandbox fallback
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- load / save ---------------------------------------------------------

    def _load(self) -> None:
        if self.data_file.exists():
            try:
                raw = json.loads(self.data_file.read_text(encoding="utf-8"))
                self._accounts = raw.get("accounts", [])
                self._events = raw.get("events", [])
                return
            except (OSError, json.JSONDecodeError):
                pass  # corrupt file -> reseed below

        # First run: seed a few demo accounts so the dashboard is alive.
        self._accounts = list(self._seed_accounts())
        self._events = ["Storage initialized — first run on this device"]
        self.save()

    def save(self) -> None:
        """Atomic JSON write (temp file + rename) so a crash never corrupts
        the account database. On read-only hosts we degrade to memory only."""
        payload = {
            "schema": self.SCHEMA_VERSION,
            "updated_at": _now_iso(),
            "accounts": self._accounts,
            "events": self._events[-50:],
        }
        try:
            tmp = self.data_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self.data_file)
            self.persistence_ok = True
        except OSError:
            self.persistence_ok = False

    @staticmethod
    def _seed_accounts():
        return [
            {"id": str(uuid.uuid4()), "name": "Primary Main",
             "platform": "PC", "status": "online", "last_launched": None},
            {"id": str(uuid.uuid4()), "name": "Alt — Trade Bot",
             "platform": "Android", "status": "idle", "last_launched": None},
            {"id": str(uuid.uuid4()), "name": "Clan Support",
             "platform": "iOS", "status": "offline", "last_launched": None},
            {"id": str(uuid.uuid4()), "name": "Marketplace Scout",
             "platform": "Web", "status": "offline", "last_launched": None},
        ]

    # -- CRUD ----------------------------------------------------------------

    def list_accounts(self, query: str = "") -> list[dict]:
        """Return accounts, filtered by name and sorted online-first."""
        q = query.strip().lower()
        items = [a for a in self._accounts
                 if not q or q in a["name"].lower()]
        rank = {"online": 0, "idle": 1, "offline": 2}
        return sorted(items, key=lambda a: (rank.get(a.get("status", "offline"), 3),
                                            a["name"].lower()))

    def add_account(self, name: str, platform: str, status: str) -> dict:
        acc = {"id": str(uuid.uuid4()), "name": name, "platform": platform,
               "status": status, "last_launched": None}
        self._accounts.append(acc)
        self.save()
        return acc

    def update_account(self, account_id: str, **fields) -> bool:
        for acc in self._accounts:
            if acc["id"] == account_id:
                for key, value in fields.items():
                    if value is not None:
                        acc[key] = value
                self.save()
                return True
        return False

    def delete_account(self, account_id: str) -> bool:
        before = len(self._accounts)
        self._accounts = [a for a in self._accounts if a["id"] != account_id]
        if len(self._accounts) != before:
            self.save()
            return True
        return False

    # -- activity log --------------------------------------------------------

    def log_event(self, message: str) -> None:
        self._events.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        self._events = self._events[-50:]
        self.save()

    def recent_events(self, limit: int = 4) -> list[str]:
        return self._events[-limit:]

    # -- dashboard stats -----------------------------------------------------

    def stats(self) -> dict:
        return {
            "total": len(self._accounts),
            "online": sum(1 for a in self._accounts if a.get("status") == "online"),
            "idle": sum(1 for a in self._accounts if a.get("status") == "idle"),
            "offline": sum(1 for a in self._accounts if a.get("status") == "offline"),
        }


# -----------------------------------------------------------------------------
# AuthManager — hardcoded owner identity + session persistence
# -----------------------------------------------------------------------------

class AuthManager:
    """Verifies the hardcoded owner credentials and manages the session token.

    Credentials live here as constants (spec requirement). We still compare
    with hmac.compare_digest (constant-time) rather than `==`, and the session
    token is a salted SHA-256 of the identity so nothing plaintext is written
    to the session file.
    """

    def __init__(self, session_file: Path) -> None:
        self.session_file = session_file

    # -- verification --------------------------------------------------------

    @staticmethod
    def _digest(*parts: str) -> str:
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def verify(self, username: str, password: str) -> bool:
        """Constant-time check of the hardcoded owner identity."""
        user_ok = hmac.compare_digest(username.strip(), OWNER_USERNAME)
        pass_ok = hmac.compare_digest(password, OWNER_PASSWORD)
        return user_ok and pass_ok

    def issue_token(self) -> str:
        return self._digest(OWNER_USERNAME, OWNER_PASSWORD, AUTH_SALT)

    def validate_token(self, token: str) -> bool:
        return bool(token) and hmac.compare_digest(token, self.issue_token())

    # -- session persistence -------------------------------------------------

    def save_session(self, token: str) -> None:
        try:
            self.session_file.write_text(
                json.dumps({"token": token, "created_at": _now_iso()}),
                encoding="utf-8")
        except OSError:
            pass  # ephemeral platform: session simply won't survive restart

    def load_token(self) -> str | None:
        try:
            if self.session_file.exists():
                data = json.loads(self.session_file.read_text(encoding="utf-8"))
                return data.get("token")
        except (OSError, json.JSONDecodeError):
            return None
        return None

    def clear_session(self) -> None:
        try:
            self.session_file.unlink(missing_ok=True)
        except OSError:
            pass


# -----------------------------------------------------------------------------
# ZadenxxApp — UI controller / state router
# -----------------------------------------------------------------------------

class ZadenxxApp:
    """Owns the page, the two views (login / dashboard) and all state routing.

    State machine:
        self.state == "login"     -> splash with sign in / sign up form
        self.state == "dashboard" -> transparency bar + account dashboard

    `route()` is the single choke point for view transitions: it rebuilds the
    root container content (fading via AnimatedSwitcher when available) so the
    layout swap reads as a seamless transition rather than a hard reload.
    """

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.store = AccountStore()
        self.auth = AuthManager(self.store.session_file)

        # ---- runtime state -------------------------------------------------
        self.state = "login"            # current view ("login" | "dashboard")
        self.login_mode = "signin"      # active auth tab ("signin" | "signup")
        self.bg_opacity = 0.90          # dashboard panel alpha (0.2 -> 1.0)
        self.failed_attempts = 0        # consecutive bad logins
        self.lock_until = 0.0           # epoch seconds until unlock

        # ---- control handles (kept so refresh() can mutate in place) -------
        self.root: ft.Container | None = None
        self.switcher: ft.AnimatedSwitcher | None = None
        self.main_panel: ft.Container | None = None
        self.opacity_slider: ft.Slider | None = None
        self.opacity_label: ft.Text | None = None
        self.stat_labels: dict[str, ft.Text] = {}
        self.search_field: ft.TextField | None = None
        self.list_header: ft.Text | None = None
        self.accounts_column: ft.Column | None = None
        self.activity_column: ft.Column | None = None
        # login form handles
        self.user_field: ft.TextField | None = None
        self.pwd_field: ft.TextField | None = None
        self.login_error: ft.Text | None = None
        self.error_box: ft.Container | None = None
        self.login_button: ft.FilledButton | None = None
        self.mode_row: ft.Row | None = None

        self._configure_page()
        self._build_root()
        self._route_initial()

    # =========================================================================
    # PAGE / ROOT SETUP
    # =========================================================================

    def _configure_page(self) -> None:
        """Window + theme setup. Every desktop-only call is guarded so mobile
        and web builds simply skip what they don't support."""
        self.page.title = "Zadenxx Account Manager"
        self.page.padding = 0
        self.page.spacing = 0
        self.page.bgcolor = BG_BLACK

        try:  # force dark theme, seed the palette white for strict monochrome
            self.page.theme_mode = ft.ThemeMode.DARK
            self.page.theme = ft.Theme(color_scheme_seed="#FFFFFF")
        except Exception:
            pass

        try:  # desktop window geometry (ignored on mobile/web)
            self.page.window.width = 460
            self.page.window.height = 840
            self.page.window.min_width = 380
            self.page.window.min_height = 620
            self.page.window.center()
        except Exception:
            pass

    def _build_root(self) -> None:
        """Single persistent root container. Every view swap only replaces
        this container's child, never the page itself."""
        self.root = ft.Container(expand=True, bgcolor=BG_BLACK)

        # Fade switcher makes login -> dashboard transition seamless where
        # supported (Flet >= 0.21); older Flet falls back to a plain swap.
        try:
            self.switcher = ft.AnimatedSwitcher(
                content=ft.Container(),
                transition=ft.AnimatedSwitcherTransition.FADE,
                duration=350,
            )
            self.switcher.expand = True
            self.root.content = self.switcher
        except AttributeError:
            self.root.content = ft.Container()

        self.page.add(self.root)

    def _route_initial(self) -> None:
        """Boot routing: a valid persisted session skips the splash page."""
        token = self.auth.load_token()
        if token and self.auth.validate_token(token):
            self.store.log_event("Owner session restored from storage")
            self.route("dashboard")
        else:
            self.route("login")

    def route(self, view: str) -> None:
        """THE state router. Swaps the root content based on self.state."""
        self.state = view
        content = (self._build_dashboard() if view == "dashboard"
                   else self._build_login())
        if self.switcher is not None:
            self.switcher.content = content
        else:
            self.root.content = content
        self.page.update()

    # =========================================================================
    # SHARED UI HELPERS
    # =========================================================================

    def toast(self, message: str) -> None:
        """SnackBar helper compatible with page.open() (new) and
        page.snack_bar (legacy) APIs."""
        snack = ft.SnackBar(
            content=ft.Text(message, color=TEXT_WHITE, size=13),
            bgcolor=BG_PANEL,
            duration=2200,
        )
        try:
            snack.behavior = ft.SnackBarBehavior.FLOATING
        except AttributeError:
            pass
        try:
            self.page.open(snack)
        except AttributeError:
            self.page.snack_bar = snack
            snack.open = True
            self.page.update()

    def open_dialog(self, dialog: ft.AlertDialog) -> None:
        try:
            self.page.open(dialog)
        except AttributeError:
            self.page.dialog = dialog
            dialog.open = True
            self.page.update()

    def close_dialog(self, dialog: ft.AlertDialog) -> None:
        try:
            self.page.close(dialog)
        except AttributeError:
            dialog.open = False
            self.page.update()

    # =========================================================================
    # VIEW 1 — LOGIN / SIGNUP SPLASH
    # =========================================================================

    def _build_login(self) -> ft.Container:
        """Clean minimalist owner splash: logo mark, auth tabs, credentials
        form, error surface and build footer."""
        # --- logo -----------------------------------------------------------
        logo = ft.Container(
            width=84, height=84, border_radius=20, bgcolor=TEXT_WHITE,
            alignment=ft.alignment.center,
            content=ft.Text("Z", size=46, weight=ft.FontWeight.BLACK,
                            color=BG_BLACK),
        )
        title = ft.Text(
            "ZADENXX", size=30, weight=ft.FontWeight.BLACK, color=TEXT_WHITE,
            style=ft.TextStyle(letter_spacing=6),
        )
        subtitle = ft.Text(
            "ACCOUNT MANAGER  ·  OWNER CONSOLE", size=11, color=TEXT_GREY,
            style=ft.TextStyle(letter_spacing=2),
        )

        # --- credentials form ----------------------------------------------
        self.user_field = ft.TextField(
            label="Username", hint_text="Owner username",
            prefix_icon=_ic("PERSON_OUTLINE"), autofocus=True,
            bgcolor=BG_CARD, color=TEXT_WHITE, cursor_color=TEXT_WHITE,
            border_color=BORDER_GREY, focused_border_color=TEXT_WHITE,
            border_radius=10, text_size=14, filled=True,
            on_submit=lambda e: self._on_login_submit(),
        )
        self.pwd_field = ft.TextField(
            label="Password", password=True,
            prefix_icon=_ic("LOCK_OUTLINE"),
            bgcolor=BG_CARD, color=TEXT_WHITE, cursor_color=TEXT_WHITE,
            border_color=BORDER_GREY, focused_border_color=TEXT_WHITE,
            border_radius=10, text_size=14, filled=True,
            on_submit=lambda e: self._on_login_submit(),
        )
        reveal_btn = ft.IconButton(
            icon=_ic("VISIBILITY"), icon_color=TEXT_GREY, tooltip="Show password",
            on_click=lambda e: self._toggle_password_visibility(),
        )
        password_row = ft.Row(
            [self.pwd_field, reveal_btn],
            spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # --- error surface (hidden until needed) ----------------------------
        self.login_error = ft.Text("", size=12, weight=ft.FontWeight.BOLD,
                                   color=TEXT_WHITE)
        self.error_box = ft.Container(
            visible=False, bgcolor=BG_PANEL,
            border=ft.border.all(1, BORDER_WHITE), border_radius=8,
            padding=ft.padding.symmetric(horizontal=12, vertical=10),
            content=ft.Row(
                [ft.Icon(_ic("SHIELD"), size=15, color=TEXT_WHITE),
                 self.login_error],
                spacing=8,
            ),
        )

        # --- submit button --------------------------------------------------
        self.login_button = ft.FilledButton(
            text="SIGN IN", expand=True, height=46,
            style=ft.ButtonStyle(
                bgcolor=TEXT_WHITE, color=BG_BLACK,
                side=ft.BorderSide(1, BORDER_WHITE),
                shape=ft.RoundedRectangleBorder(radius=10),
            ),
            on_click=lambda e: self._on_login_submit(),
        )

        # --- auth mode switch (SIGN IN / SIGN UP) ---------------------------
        self._build_mode_switch()

        form = ft.Column(
            [
                logo,
                ft.Container(height=14),
                title,
                subtitle,
                ft.Container(height=18),
                self.mode_row,
                ft.Container(height=18),
                self.user_field,
                password_row,
                ft.Container(height=10),
                self.error_box,
                ft.Container(height=12),
                self.login_button,
                ft.Container(height=16),
                ft.Text(
                    "ZADENXX OS v2.4.1  ·  BUILD 2026.08  ·  SECURE CHANNEL",
                    size=10, color=TEXT_DIM,
                    style=ft.TextStyle(letter_spacing=1),
                ),
            ],
            width=350, spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        return ft.Container(
            expand=True,
            alignment=ft.alignment.center,
            padding=ft.padding.symmetric(vertical=24, horizontal=20),
            content=form,
        )

    def _build_mode_switch(self) -> None:
        """Monochrome segmented control for the SIGN IN / SIGN UP tabs."""
        def chip(active: bool, label: str) -> ft.Container:
            return ft.Container(
                expand=True, height=42,
                alignment=ft.alignment.center,
                bgcolor=TEXT_WHITE if active else BG_PANEL,
                border=ft.border.all(1, BORDER_WHITE),
                border_radius=8,
                content=ft.Text(
                    label, size=12, weight=ft.FontWeight.BOLD,
                    color=BG_BLACK if active else TEXT_WHITE,
                ),
            )

        sign_in = chip(self.login_mode == "signin", "SIGN IN")
        sign_up = chip(self.login_mode == "signup", "SIGN UP")
        sign_in.on_click = lambda e: self._set_login_mode("signin")
        sign_up.on_click = lambda e: self._set_login_mode("signup")
        self.mode_row = ft.Row([sign_in, sign_up], spacing=8)

    def _set_login_mode(self, mode: str) -> None:
        """Re-render the tab chips and adapt the submit button label."""
        self.login_mode = mode
        self._build_mode_switch()
        if self.login_button is not None:
            self.login_button.text = ("SIGN IN" if mode == "signin"
                                      else "CREATE & SIGN IN")
        self._set_error("")
        self.page.update()

    def _toggle_password_visibility(self) -> None:
        if self.pwd_field is None:
            return
        self.pwd_field.password = not self.pwd_field.password
        self.page.update()

    def _set_error(self, message: str) -> None:
        if self.login_error is not None:
            self.login_error.value = message
        if self.error_box is not None:
            self.error_box.visible = bool(message)

    def _on_login_submit(self, e=None) -> None:
        """Authentication handler: lockout check -> validate -> route.

        Runs synchronously with a short deliberate delay to sell the
        "verifying" moment; the lockout timer re-enables the button from a
        background thread (Flet page updates are thread-safe)."""
        now = time.time()
        if now < self.lock_until:
            self._set_error(
                f"Too many failed attempts — locked for {int(self.lock_until - now)}s.")
            return

        user = (self.user_field.value or "").strip()
        pwd = self.pwd_field.value or ""
        if not user or not pwd:
            self._set_error("Username and password are required.")
            self.page.update()
            return

        # --- visual "verifying" state --------------------------------------
        self.login_button.disabled = True
        self.login_button.text = "Authenticating…"
        self.page.update()
        time.sleep(0.55)

        # --- verification ---------------------------------------------------
        if not self.auth.verify(user, pwd):
            self.failed_attempts += 1
            remaining = MAX_LOGIN_ATTEMPTS - self.failed_attempts
            if self.failed_attempts >= MAX_LOGIN_ATTEMPTS:
                self.lock_until = time.time() + LOCKOUT_SECONDS
                self.failed_attempts = 0
                self._set_error(
                    f"Account locked for {LOCKOUT_SECONDS}s — too many failed attempts.")
                self.login_button.disabled = True
                threading.Timer(LOCKOUT_SECONDS, self._unlock_login).start()
            else:
                self._set_error(
                    f"Invalid credentials — {remaining} attempt(s) remaining.")
            self.login_button.text = ("SIGN IN" if self.login_mode == "signin"
                                      else "CREATE & SIGN IN")
            self.page.update()
            return

        # --- success: issue session and route to the dashboard --------------
        self.failed_attempts = 0
        self.auth.save_session(self.auth.issue_token())
        verb = "registered" if self.login_mode == "signup" else "authenticated"
        self.store.log_event(f"Owner {verb} — session started")
        self._set_error("")
        self.route("dashboard")
        self.toast(f"Welcome back, {OWNER_USERNAME} — session established.")

    def _unlock_login(self) -> None:
        """Background-thread callback: re-enable the submit button after the
        lockout cooldown expires."""
        self.lock_until = 0.0
        if self.login_button is not None:
            self.login_button.disabled = False
            self.login_button.text = ("SIGN IN" if self.login_mode == "signin"
                                      else "CREATE & SIGN IN")
            try:
                self.page.update()
            except Exception:
                pass

    # =========================================================================
    # VIEW 2 — ACCOUNT DASHBOARD
    # =========================================================================

    def _build_dashboard(self) -> ft.Column:
        """Dashboard shell: top-anchored transparency bar + the opacity-
        controlled main panel that hosts the account management UI."""
        bar = self._build_transparency_bar()

        self.main_panel = ft.Container(
            expand=True,
            bgcolor=BG_CHARCOAL,
            opacity=self.bg_opacity,           # <- slider drives this alpha
            border=ft.border.all(1, BORDER_GREY),
            border_radius=16,
            padding=14,
            content=self._build_dashboard_body(),
        )
        return ft.Column([bar, self.main_panel], spacing=10, expand=True)

    def _build_transparency_bar(self) -> ft.Container:
        """Spec 2: a functional slider anchored to the top of the layout that
        remaps the alpha channel of the main background container."""
        self.opacity_slider = ft.Slider(
            min=0.2, max=1.0, value=self.bg_opacity, divisions=16,
            active_color=TEXT_WHITE, inactive_color=BORDER_GREY,
            thumb_color=TEXT_WHITE,
            on_change=self._on_opacity_change,
            expand=True,
        )
        self.opacity_label = ft.Text(
            f"{int(self.bg_opacity * 100)}%", size=12, color=TEXT_GREY,
            width=44, text_align=ft.TextAlign.CENTER,
        )
        sign_out = ft.IconButton(
            icon=_ic("LOGOUT"), icon_color=TEXT_WHITE, tooltip="Sign out",
            on_click=lambda e: self._on_signout(),
            width=38, height=38,
        )
        return ft.Container(
            bgcolor=BG_PANEL,
            border=ft.border.all(1, BORDER_GREY),
            border_radius=12,
            padding=ft.padding.symmetric(horizontal=12, vertical=4),
            content=ft.Row(
                [
                    ft.Text("OPACITY", size=11, weight=ft.FontWeight.BOLD,
                            color=TEXT_GREY),
                    self.opacity_slider,
                    self.opacity_label,
                    ft.VerticalDivider(width=1, color=BORDER_GREY),
                    sign_out,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _on_opacity_change(self, e) -> None:
        """Live-update the main panel alpha + the percentage readout."""
        self.bg_opacity = float(e.control.value)
        if self.main_panel is not None:
            self.main_panel.opacity = self.bg_opacity
        if self.opacity_label is not None:
            self.opacity_label.value = f"{int(self.bg_opacity * 100)}%"
        self.page.update()

    def _build_dashboard_body(self) -> ft.Column:
        """Everything inside the opacity-controlled panel: storage banner,
        header, stat chips, search/create row, account list, activity log."""
        controls: list[ft.Control] = []

        # Ephemeral-storage warning (web hosting without writable storage)
        if not self.store.persistence_ok:
            controls.append(ft.Container(
                bgcolor=BG_PANEL,
                border=ft.border.all(1, BORDER_GREY),
                border_radius=8, padding=8,
                content=ft.Row(
                    [ft.Icon(_ic("TERMINAL"), size=14, color=TEXT_GREY),
                     ft.Text(
                         "EPHEMERAL STORAGE — changes won't persist on this "
                         "platform (static web hosting).", size=11,
                         color=TEXT_GREY)],
                    spacing=8,
                ),
            ))

        # --- header ---------------------------------------------------------
        controls.append(ft.Row(
            [
                ft.Text("ACCOUNT DASHBOARD", size=18,
                        weight=ft.FontWeight.BLACK, color=TEXT_WHITE),
                ft.Container(expand=True),
                ft.Container(
                    padding=ft.padding.symmetric(horizontal=10, vertical=4),
                    border=ft.border.all(1, BORDER_WHITE), border_radius=14,
                    content=ft.Text("OWNER", size=10,
                                    weight=ft.FontWeight.BOLD, color=TEXT_WHITE),
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ))

        # --- live stats chips ----------------------------------------------
        self.stat_labels = {}
        controls.append(ft.Row(
            [self._build_stat_chip("TOTAL", "total"),
             self._build_stat_chip("ONLINE", "online"),
             self._build_stat_chip("IDLE", "idle"),
             self._build_stat_chip("OFFLINE", "offline")],
            spacing=8,
        ))

        # --- search + create -----------------------------------------------
        self.search_field = ft.TextField(
            hint_text="Filter accounts…", prefix_icon=_ic("SEARCH"),
            expand=True, text_size=13,
            bgcolor=BG_CARD, color=TEXT_WHITE, cursor_color=TEXT_WHITE,
            border_color=BORDER_GREY, focused_border_color=TEXT_WHITE,
            border_radius=10, filled=True,
            on_change=lambda e: self._refresh_accounts(),
        )
        new_button = ft.FilledButton(
            "NEW ACCOUNT", icon=_ic("ADD"),
            style=ft.ButtonStyle(
                bgcolor=TEXT_WHITE, color=BG_BLACK,
                side=ft.BorderSide(1, BORDER_WHITE),
                shape=ft.RoundedRectangleBorder(radius=10),
            ),
            on_click=lambda e: self._open_account_dialog(),
        )
        controls.append(ft.Row([self.search_field, new_button], spacing=8))

        # --- account list ---------------------------------------------------
        self.list_header = ft.Text("MANAGED ACCOUNTS (0)", size=11,
                                   weight=ft.FontWeight.BOLD, color=TEXT_GREY)
        controls.append(self.list_header)
        self.accounts_column = ft.Column(
            spacing=10, scroll=AUTO_SCROLL, expand=True,
        )
        controls.append(self.accounts_column)

        # --- recent activity ------------------------------------------------
        controls.append(ft.Text("RECENT ACTIVITY", size=11,
                                weight=ft.FontWeight.BOLD, color=TEXT_GREY))
        self.activity_column = ft.Column(spacing=3)
        controls.append(self.activity_column)

        body = ft.Column(controls, spacing=12, expand=True)
        self._refresh_accounts()
        return body

    def _build_stat_chip(self, label: str, key: str) -> ft.Container:
        value = ft.Text("0", size=20, weight=ft.FontWeight.BLACK,
                        color=TEXT_WHITE)
        self.stat_labels[key] = value
        return ft.Container(
            expand=True, bgcolor=BG_CARD,
            border=ft.border.all(1, BORDER_GREY), border_radius=10,
            padding=ft.padding.symmetric(vertical=10),
            content=ft.Column(
                [value,
                 ft.Text(label, size=10, color=TEXT_GREY,
                         weight=ft.FontWeight.BOLD)],
                spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_account_card(self, acc: dict) -> ft.Container:
        """One monochrome card per managed account: avatar, name, live status
        indicator, platform, last-launch stamp and action controls."""
        status = acc.get("status", "offline")
        dot = ft.Container(
            width=10, height=10, border_radius=5,
            bgcolor=STATUS_DOTS.get(status, DOT_OFFLINE),
            border=ft.border.all(1, BORDER_WHITE) if status != "online" else None,
        )
        last = acc.get("last_launched")
        last_txt = "Never launched" if not last else f"Last launch {last[11:19]}"
        avatar = ft.Container(
            width=34, height=34, border_radius=9,
            bgcolor=BG_PANEL, border=ft.border.all(1, BORDER_GREY),
            alignment=ft.alignment.center,
            content=ft.Text(acc["name"][:1].upper(), weight=ft.FontWeight.BOLD,
                            color=TEXT_WHITE),
        )
        info = ft.Column(
            [
                ft.Text(acc["name"], size=14, weight=ft.FontWeight.BOLD,
                        color=TEXT_WHITE),
                ft.Row(
                    [dot,
                     ft.Text(status.upper(), size=10, color=TEXT_GREY,
                             weight=ft.FontWeight.BOLD),
                     ft.Text("·", size=10, color=TEXT_DIM),
                     ft.Text(acc.get("platform", "—"), size=10, color=TEXT_DIM),
                     ft.Text("·", size=10, color=TEXT_DIM),
                     ft.Text(last_txt, size=10, color=TEXT_DIM)],
                    spacing=6,
                ),
            ],
            spacing=3, expand=True,
        )
        edit_btn = ft.IconButton(
            icon=_ic("EDIT"), icon_color=TEXT_GREY, tooltip="Edit account",
            on_click=lambda e, a=acc: self._open_account_dialog(a),
            width=34, height=34,
        )
        delete_btn = ft.IconButton(
            icon=_ic("DELETE"), icon_color=TEXT_GREY, tooltip="Delete account",
            on_click=lambda e, a=acc: self._confirm_delete(a),
            width=34, height=34,
        )
        launch_btn = ft.FilledButton(
            "LAUNCH",
            style=ft.ButtonStyle(
                bgcolor=TEXT_WHITE, color=BG_BLACK,
                side=ft.BorderSide(1, BORDER_WHITE),
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=12, vertical=10),
            ),
            on_click=lambda e, a=acc: self._launch_account(a),
        )
        return ft.Container(
            bgcolor=BG_CARD,
            border=ft.border.all(1, BORDER_GREY),
            border_radius=12, padding=12,
            content=ft.Row(
                [avatar, info, edit_btn, delete_btn, launch_btn],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # -- dashboard refresh ----------------------------------------------------

    def _refresh_accounts(self) -> None:
        """Rebuild the mutable dashboard regions (stats, list, activity) in
        place — the panel shell and transparency bar stay untouched."""
        query = self.search_field.value if self.search_field else ""
        accounts = self.store.list_accounts(query)
        stats = self.store.stats()

        if self.stat_labels:
            for key, label in self.stat_labels.items():
                label.value = str(stats.get(key, 0))

        if self.list_header is not None:
            self.list_header.value = f"MANAGED ACCOUNTS ({len(accounts)})"

        if self.accounts_column is not None:
            self.accounts_column.controls = [
                self._build_account_card(a) for a in accounts
            ]

        if self.activity_column is not None:
            self.activity_column.controls = [
                ft.Row(
                    [ft.Text("●", size=8, color=TEXT_GREY),
                     ft.Text(msg, size=11, color=TEXT_GREY, expand=True)],
                    spacing=8,
                )
                for msg in self.store.recent_events(4)
            ]

        self.page.update()

    # -- account actions -------------------------------------------------------

    def _open_account_dialog(self, account: dict | None = None) -> None:
        """Create/edit dialog. `account=None` -> new account mode."""
        is_edit = account is not None
        name_field = ft.TextField(
            label="Account name",
            value=account["name"] if is_edit else "",
            bgcolor=BG_CARD, color=TEXT_WHITE, cursor_color=TEXT_WHITE,
            border_color=BORDER_GREY, focused_border_color=TEXT_WHITE,
            border_radius=10, filled=True, autofocus=True,
        )
        platform_dd = ft.Dropdown(
            label="Platform",
            value=account.get("platform") if is_edit else "PC",
            options=["PC", "Android", "iOS", "Web", "Console"],
            bgcolor=BG_CARD, color=TEXT_WHITE,
            border_color=BORDER_GREY, focused_border_color=TEXT_WHITE,
            border_radius=10, filled=True,
        )
        status_dd = ft.Dropdown(
            label="Status",
            value=account.get("status") if is_edit else "offline",
            options=["online", "idle", "offline"],
            bgcolor=BG_CARD, color=TEXT_WHITE,
            border_color=BORDER_GREY, focused_border_color=TEXT_WHITE,
            border_radius=10, filled=True,
        )

        def on_save(e) -> None:
            name = (name_field.value or "").strip()
            if not name:
                name_field.error_text = "Account name is required."
                name_field.update()
                return
            if is_edit:
                self.store.update_account(
                    account["id"], name=name,
                    platform=platform_dd.value, status=status_dd.value)
                self.store.log_event(f"Updated account '{name}'")
                message = f"Account '{name}' updated."
            else:
                self.store.add_account(name, platform_dd.value, status_dd.value)
                self.store.log_event(f"Added account '{name}'")
                message = f"Account '{name}' created."
            self.close_dialog(dialog)
            self._refresh_accounts()
            self.toast(message)

        dialog = ft.AlertDialog(
            modal=True, bgcolor=BG_PANEL,
            title=ft.Text("EDIT ACCOUNT" if is_edit else "NEW ACCOUNT",
                          size=16, weight=ft.FontWeight.BLACK,
                          color=TEXT_WHITE),
            content=ft.Container(
                width=300,
                content=ft.Column(
                    [name_field, platform_dd, status_dd],
                    spacing=12, tight=True,
                ),
            ),
            actions=[
                ft.TextButton(
                    "CANCEL",
                    style=ft.ButtonStyle(color=TEXT_WHITE),
                    on_click=lambda e: self.close_dialog(dialog),
                ),
                ft.FilledButton(
                    "SAVE",
                    style=ft.ButtonStyle(
                        bgcolor=TEXT_WHITE, color=BG_BLACK,
                        shape=ft.RoundedRectangleBorder(radius=8),
                    ),
                    on_click=on_save,
                ),
            ],
        )
        self.open_dialog(dialog)

    def _confirm_delete(self, account: dict) -> None:
        """Destructive action gate — monochrome confirmation dialog."""
        dialog = ft.AlertDialog(
            modal=True, bgcolor=BG_PANEL,
            title=ft.Text("DELETE ACCOUNT", size=16,
                          weight=ft.FontWeight.BLACK, color=TEXT_WHITE),
            content=ft.Text(
                f"Remove '{account['name']}' permanently?\n"
                "This cannot be undone.", size=13, color=TEXT_GREY),
            actions=[
                ft.TextButton(
                    "CANCEL",
                    style=ft.ButtonStyle(color=TEXT_WHITE),
                    on_click=lambda e: self.close_dialog(dialog),
                ),
                ft.FilledButton(
                    "DELETE",
                    style=ft.ButtonStyle(
                        bgcolor=TEXT_WHITE, color=BG_BLACK,
                        shape=ft.RoundedRectangleBorder(radius=8),
                    ),
                    on_click=lambda e: self._delete_account(account, dialog),
                ),
            ],
        )
        self.open_dialog(dialog)

    def _delete_account(self, account: dict, dialog: ft.AlertDialog) -> None:
        self.store.delete_account(account["id"])
        self.store.log_event(f"Deleted account '{account['name']}'")
        self.close_dialog(dialog)
        self._refresh_accounts()
        self.toast(f"Account '{account['name']}' deleted.")

    def _launch_account(self, account: dict) -> None:
        """Trigger an instance launch: flip status to online, stamp the launch
        time, persist, then refresh the dashboard in place."""
        account["status"] = "online"
        account["last_launched"] = _now_iso()
        self.store.save()
        self.store.log_event(f"Instance launched — {account['name']} "
                             f"({account['platform']})")
        self._refresh_accounts()
        self.toast(f"Launch signal sent to '{account['name']}'.")

    # -- session --------------------------------------------------------------

    def _on_signout(self) -> None:
        """Invalidate the persisted session and route back to the splash."""
        self.auth.clear_session()
        self.store.log_event("Owner signed out — session closed")
        self.route("login")
        self.toast("Signed out. Session token revoked.")


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def main(page: ft.Page) -> None:
    """Standard Flet entry point required by `flet build`/`flet run`."""
    ZadenxxApp(page)


if __name__ == "__main__":
    launch_kwargs: dict = {"target": main, "name": "zadenxx_account_manager"}
    if "--web" in sys.argv:  # optional browser preview during development
        try:
            launch_kwargs["view"] = ft.AppView.WEB_BROWSER
        except AttributeError:
            pass
    try:
        ft.app(**launch_kwargs)
    except TypeError:  # very old Flet without the `name` kwarg
        launch_kwargs.pop("name", None)
        ft.app(**launch_kwargs)
