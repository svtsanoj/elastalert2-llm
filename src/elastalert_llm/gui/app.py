"""Tkinter GUI: chat with the agent on the left; rule editor and tool trace on the right.

Run with:  elastalert2-llm   (or: python -m elastalert_llm)
"""
import json
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from elastalert_llm.agent import Agent, ToolBox
from elastalert_llm.agent.tools import es_client_from_settings
from elastalert_llm.es import ESError
from elastalert_llm.llm import LLMError, OllamaBackend
from elastalert_llm.verify import validate_rule_text

SETTINGS_FILE = Path.home() / ".elastalert2-llm.json"
EXAMPLE_RULES = Path(__file__).resolve().parents[3] / "examples" / "rules"
YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL)
CODE_FONT = ("Consolas", 10)

DEFAULTS = {
    "ollama_url": "http://localhost:11434",
    "model": "qwen2.5-coder:7b",
    "num_ctx": 8192,
    "temperature": 0.2,
    "use_tools": True,
    "rules_dir": str(EXAMPLE_RULES) if EXAMPLE_RULES.is_dir() else "",
    "es_url": "http://localhost:9200",
    "es_user": "elastic",
    "es_ca": "",
}

STARTERS = {
    "Create": "I want an alert that tells me when ... (what should be noticed, how many / how fast, and where to send it).",
    "Debug": "My rule '<rule name>' didn't fire (or is too noisy) around <time>. Please find out why.",
    "Review": "Review all my rules. Which ones are broken or risky, and how do I fix them?",
}


def load_settings():
    settings = dict(DEFAULTS)
    try:
        settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return settings


class App:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings()
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.agent = None
        self.last_yaml = None

        root.title("elastalert2-llm")
        root.geometry("1400x860")
        root.minsize(900, 600)
        self._build_toolbar()
        self._build_body()
        self._new_chat()
        self.refresh_models()
        root.after(50, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # --- layout -----------------------------------------------------------

    def _build_toolbar(self):
        s = self.settings
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(fill="x")

        ttk.Label(bar, text="Ollama").grid(row=0, column=0, sticky="w")
        self.ollama_url = tk.StringVar(value=s["ollama_url"])
        ttk.Entry(bar, textvariable=self.ollama_url, width=24).grid(row=0, column=1, padx=(4, 10))
        ttk.Label(bar, text="Model").grid(row=0, column=2, sticky="w")
        self.model = tk.StringVar(value=s["model"])
        self.model_box = ttk.Combobox(bar, textvariable=self.model, width=28)
        self.model_box.grid(row=0, column=3, padx=4)
        ttk.Button(bar, text="⟳", width=3, command=self.refresh_models).grid(row=0, column=4, padx=(0, 10))
        ttk.Label(bar, text="Context").grid(row=0, column=5, sticky="w")
        self.num_ctx = tk.IntVar(value=s["num_ctx"])
        ttk.Combobox(bar, textvariable=self.num_ctx, values=[4096, 8192, 16384, 32768], width=7).grid(row=0, column=6, padx=(4, 10))
        ttk.Label(bar, text="Temp").grid(row=0, column=7, sticky="w")
        self.temperature = tk.DoubleVar(value=s["temperature"])
        ttk.Spinbox(bar, textvariable=self.temperature, from_=0, to=1.5, increment=0.1, width=5).grid(row=0, column=8, padx=(4, 10))
        self.use_tools = tk.BooleanVar(value=s["use_tools"])
        ttk.Checkbutton(bar, text="Tools", variable=self.use_tools).grid(row=0, column=9, padx=(0, 10))
        self.status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status, foreground="#555").grid(row=0, column=10, sticky="e")
        bar.columnconfigure(10, weight=1)

        ttk.Label(bar, text="Rules").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.rules_dir = tk.StringVar(value=s["rules_dir"])
        ttk.Entry(bar, textvariable=self.rules_dir, width=50).grid(row=1, column=1, columnspan=3, sticky="we", padx=4, pady=(6, 0))
        ttk.Button(bar, text="Browse…", command=self._browse_rules).grid(row=1, column=4, columnspan=2, sticky="w", pady=(6, 0))
        es = ttk.Frame(bar)
        es.grid(row=1, column=6, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Label(es, text="ES URL").pack(side="left")
        self.es_url = tk.StringVar(value=s["es_url"])
        ttk.Entry(es, textvariable=self.es_url, width=24).pack(side="left", padx=4)
        ttk.Label(es, text="User").pack(side="left")
        self.es_user = tk.StringVar(value=s["es_user"])
        ttk.Entry(es, textvariable=self.es_user, width=10).pack(side="left", padx=4)
        ttk.Label(es, text="Password").pack(side="left")
        self.es_password = tk.StringVar(value="")  # never saved to disk
        ttk.Entry(es, textvariable=self.es_password, width=14, show="•").pack(side="left", padx=4)
        ttk.Label(es, text="CA cert").pack(side="left")
        self.es_ca = tk.StringVar(value=s["es_ca"])
        ttk.Entry(es, textvariable=self.es_ca, width=16).pack(side="left", padx=4)
        ttk.Button(es, text="…", width=3, command=self._browse_ca).pack(side="left")
        ttk.Button(es, text="Test", command=self.test_es).pack(side="left", padx=(6, 0))

    def _build_body(self):
        panes = ttk.PanedWindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Left: conversation
        left = ttk.Frame(panes)
        starters = ttk.Frame(left)
        starters.pack(fill="x", pady=(0, 4))
        for label, text in STARTERS.items():
            ttk.Button(starters, text=label, command=lambda t=text: self._set_input(t)).pack(side="left", padx=(0, 4))
        ttk.Button(starters, text="Explain editor YAML", command=self.explain_editor).pack(side="left", padx=(0, 4))
        ttk.Button(starters, text="New chat", command=self._new_chat).pack(side="right")

        self.chat = ScrolledText(left, wrap="word", font=("Segoe UI", 10), state="disabled", padx=8, pady=6)
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_configure("user_head", foreground="#1a5fb4", font=("Segoe UI", 10, "bold"), spacing1=10)
        self.chat.tag_configure("bot_head", foreground="#26a269", font=("Segoe UI", 10, "bold"), spacing1=10)
        self.chat.tag_configure("tool", foreground="#8a6d00", font=("Consolas", 9))
        self.chat.tag_configure("error", foreground="#c01c28")
        self.chat.tag_configure("note", foreground="#777", font=("Segoe UI", 9, "italic"))

        entry = ttk.Frame(left)
        entry.pack(fill="x", pady=(6, 0))
        self.input = tk.Text(entry, height=4, wrap="word", font=("Segoe UI", 10), undo=True)
        self.input.pack(side="left", fill="both", expand=True)
        self.input.bind("<Control-Return>", lambda e: (self.send(), "break")[1])
        buttons = ttk.Frame(entry)
        buttons.pack(side="left", fill="y", padx=(6, 0))
        self.send_btn = ttk.Button(buttons, text="Send\n(Ctrl+Enter)", command=self.send)
        self.send_btn.pack(fill="x")
        self.stop_btn = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(fill="x", pady=(4, 0))
        panes.add(left, weight=3)

        # Right: rule editor and tool trace
        tabs = ttk.Notebook(panes)
        editor_tab = ttk.Frame(tabs, padding=4)
        tools = ttk.Frame(editor_tab)
        tools.pack(fill="x", pady=(0, 4))
        for label, cmd in [("Open…", self.open_rule), ("Save as…", self.save_rule), ("Validate", self.validate_editor),
                           ("Use last YAML from chat", self.use_last_yaml)]:
            ttk.Button(tools, text=label, command=cmd).pack(side="left", padx=(0, 4))
        vertical = ttk.PanedWindow(editor_tab, orient="vertical")
        vertical.pack(fill="both", expand=True)
        self.editor = ScrolledText(vertical, wrap="none", font=CODE_FONT, undo=True, height=20)
        self.validation = ScrolledText(vertical, wrap="word", font=CODE_FONT, height=8, state="disabled")
        self.validation.tag_configure("ok", foreground="#26a269")
        self.validation.tag_configure("bad", foreground="#c01c28")
        self.validation.tag_configure("warn", foreground="#8a6d00")
        vertical.add(self.editor, weight=3)
        vertical.add(self.validation, weight=1)
        tabs.add(editor_tab, text="Rule YAML")

        self.trace = ScrolledText(tabs, wrap="word", font=("Consolas", 9), state="disabled")
        self.trace.tag_configure("call", foreground="#1a5fb4", font=("Consolas", 9, "bold"))
        tabs.add(self.trace, text="Tool trace")
        panes.add(tabs, weight=2)

    # --- actions ----------------------------------------------------------

    def refresh_models(self):
        try:
            models = OllamaBackend(self.ollama_url.get()).list_models()
        except LLMError as e:
            self.status.set("Ollama not reachable")
            self._append(f"{e}\n", "error")
            return
        self.model_box["values"] = models
        if models and self.model.get() not in models:
            self.model.set(models[0])
        self.status.set(f"{len(models)} model(s) available")

    def send(self):
        text = self.input.get("1.0", "end").strip()
        if not text or self.worker:
            return
        self.input.delete("1.0", "end")
        self._start(text)

    def explain_editor(self):
        yaml_text = self.editor.get("1.0", "end").strip()
        if not yaml_text:
            messagebox.showinfo("Explain", "The Rule YAML editor is empty. Open or paste a rule first.")
            return
        self._start(f"Explain this rule in plain English for a non-technical owner, and point out any problems:\n```yaml\n{yaml_text}\n```")

    def stop(self):
        self.stop_event.set()

    def _start(self, text):
        if self.worker:
            return
        self._save_settings()
        agent = self._agent()
        self._append("You\n", "user_head")
        self._append(text + "\n")
        self.stop_event.clear()
        self.send_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.set("Thinking… (prompt processing is slow on CPU)")
        self.worker = threading.Thread(target=self._run_agent, args=(agent, text), daemon=True)
        self.worker.start()

    def _run_agent(self, agent, text):
        try:
            agent.run(text, emit=lambda kind, data: self.events.put((kind, data)), stop_event=self.stop_event)
        except LLMError as e:
            self.events.put(("error", str(e)))
        except Exception as e:  # keep the GUI alive whatever happens
            self.events.put(("error", f"{type(e).__name__}: {e}"))
        finally:
            self.events.put(("finished", None))

    def _agent(self):
        """Reuse the conversation, but pick up any toolbar changes."""
        toolbox = ToolBox(self.rules_dir.get() or None, self._es_client())
        options = {"num_ctx": int(self.num_ctx.get()), "temperature": float(self.temperature.get())}
        backend = OllamaBackend(self.ollama_url.get())
        if self.agent is None:
            self.agent = Agent(backend, self.model.get(), toolbox, options=options)
        self.agent.backend, self.agent.model, self.agent.toolbox = backend, self.model.get(), toolbox
        self.agent.options, self.agent.use_tools = options, self.use_tools.get()
        return self.agent

    def _new_chat(self):
        if self.worker:
            return
        if self.agent:
            self.agent.reset()
        for widget in (self.chat, self.trace):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.configure(state="disabled")
        self._append("Ask me to create, debug, review or explain an ElastAlert rule. "
                     "Tool calls I make are listed in the Tool trace tab.\n", "note")

    # --- rule editor ------------------------------------------------------

    def open_rule(self):
        path = filedialog.askopenfilename(initialdir=self.rules_dir.get() or None, filetypes=[("YAML", "*.yaml *.yml"), ("All", "*.*")])
        if path:
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", Path(path).read_text(encoding="utf-8"))
            self.validate_editor()

    def save_rule(self):
        path = filedialog.asksaveasfilename(initialdir=self.rules_dir.get() or None, defaultextension=".yaml",
                                            filetypes=[("YAML", "*.yaml *.yml")])
        if path:
            Path(path).write_text(self.editor.get("1.0", "end-1c"), encoding="utf-8")
            self.status.set(f"Saved {Path(path).name}")

    def validate_editor(self):
        result = validate_rule_text(self.editor.get("1.0", "end"), self.rules_dir.get() or None)
        box = self.validation
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", "✔ Valid ElastAlert rule\n" if result["ok"] else "✘ Invalid rule\n", "ok" if result["ok"] else "bad")
        for e in result["errors"]:
            box.insert("end", f"  error: {e}\n", "bad")
        for w in result["warnings"]:
            box.insert("end", f"  hint: {w}\n", "warn")
        box.configure(state="disabled")

    def use_last_yaml(self):
        if not self.last_yaml:
            messagebox.showinfo("No YAML yet", "The assistant hasn't produced a ```yaml block in this chat yet.")
            return
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self.last_yaml)
        self.validate_editor()

    def _es_client(self):
        return es_client_from_settings(self.es_url.get(), self.es_user.get(), self.es_password.get(), self.es_ca.get().strip() or None)

    def test_es(self):
        client = self._es_client()
        if client is None:
            messagebox.showwarning("Elasticsearch", "Enter the ES URL first.")
            return
        try:
            info = client.get("/")
        except ESError as e:
            messagebox.showerror("Elasticsearch", str(e))
            return
        self.status.set(f"Connected to Elasticsearch {info['version']['number']} ({info.get('cluster_name', '')})")

    def _browse_ca(self):
        path = filedialog.askopenfilename(filetypes=[("Certificates", "*.crt *.pem"), ("All", "*.*")])
        if path:
            self.es_ca.set(path)

    def _browse_rules(self):
        path = filedialog.askdirectory(initialdir=self.rules_dir.get() or None)
        if path:
            self.rules_dir.set(path)

    # --- events from the worker thread -------------------------------------

    def _poll(self):
        try:
            while True:
                self._handle(*self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _handle(self, kind, data):
        if kind == "assistant_start":
            self._append("Assistant\n", "bot_head")
            self.chat.mark_set("turn_start", "end-1c")
            self.chat.mark_gravity("turn_start", "left")
        elif kind == "token":
            self._append(data)
        elif kind == "retract":
            self.chat.configure(state="normal")
            self.chat.delete("turn_start", "end-1c")
            self.chat.configure(state="disabled")
        elif kind == "tool_call":
            args = json.dumps(data["arguments"], ensure_ascii=False)
            self._append(f"→ {data['name']}({args[:160]}{'…' if len(args) > 160 else ''})\n", "tool")
            self._trace(f"\n▶ {data['name']}\n", "call")
            self._trace(json.dumps(data["arguments"], indent=2, ensure_ascii=False) + "\n")
            self.status.set(f"Running {data['name']}…")
        elif kind == "tool_result":
            self._append(f"← {data['name']}: {_preview(data['result'])}\n", "tool")
            self._trace(_pretty(data["result"]) + "\n")
            self.status.set("Thinking…")
        elif kind == "stats":
            self.status.set(f"prompt {data.prompt_tokens} tok in {data.prompt_seconds:.1f}s · "
                            f"output {data.output_tokens} tok at {data.tokens_per_second:.1f} tok/s")
        elif kind == "done":
            blocks = YAML_BLOCK.findall(data or "")
            if blocks:
                self.last_yaml = blocks[-1].strip() + "\n"
                self._append("(Rule YAML found: click 'Use last YAML from chat' to load it into the editor.)\n", "note")
            self._append("\n")
        elif kind == "error":
            self._append(f"\n{data}\n", "error")
        elif kind == "finished":
            self.worker = None
            self.send_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")

    def _append(self, text, tag=None):
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag or ())
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _trace(self, text, tag=None):
        self.trace.configure(state="normal")
        self.trace.insert("end", text, tag or ())
        self.trace.configure(state="disabled")
        self.trace.see("end")

    def _set_input(self, text):
        self.input.delete("1.0", "end")
        self.input.insert("1.0", text)
        self.input.focus_set()

    def _save_settings(self):
        settings = {"ollama_url": self.ollama_url.get(), "model": self.model.get(), "num_ctx": int(self.num_ctx.get()),
                    "temperature": float(self.temperature.get()), "use_tools": self.use_tools.get(),
                    "rules_dir": self.rules_dir.get(), "es_url": self.es_url.get(), "es_user": self.es_user.get(),
                    "es_ca": self.es_ca.get()}
        try:
            SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _on_close(self):
        self.stop_event.set()
        self._save_settings()
        self.root.destroy()


def _preview(result_json):
    try:
        data = json.loads(result_json)
    except ValueError:
        return f"{len(result_json)} chars"
    if isinstance(data, dict) and "error" in data:
        return f"error: {str(data['error'])[:120]}"
    if isinstance(data, dict) and "ok" in data:
        return "valid" if data["ok"] else f"invalid: {'; '.join(data['errors'])[:120]}"
    return f"{len(result_json)} chars"


def _pretty(result_json):
    try:
        return json.dumps(json.loads(result_json), indent=2, ensure_ascii=False)
    except ValueError:
        return result_json


def main():
    try:  # crisp text on high-DPI Windows displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    ttk.Style().theme_use("vista" if "vista" in ttk.Style().theme_names() else "clam")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
