# -*- coding: utf-8 -*-
"""
JARVIS AI - single-file Android assistant (Kivy + pyjnius).

Run modes:
  python main.py --selftest   -> tests all non-UI logic (works in Termux, no Kivy needed)
  python main.py --init       -> writes buildozer.spec + GitHub Actions workflow
  python main.py              -> starts the UI (needs Kivy: desktop or the built APK)
"""
import os, sys, re, ast, json, math, shutil, sqlite3, socket, threading, ctypes
import datetime, ssl, html, traceback, urllib.request, urllib.parse

try:
    from kivy.utils import platform
except Exception:
    platform = sys.platform
ANDROID = platform == "android"

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.expanduser("~"), ".jarvis_ai")
PROJECTS = BACKUPS = DB_PATH = ""


# ============================================================ 1. PATHS + DATABASE
def init_paths(d=None):
    global DATA, PROJECTS, BACKUPS, DB_PATH
    if d:
        DATA = d
    PROJECTS = os.path.join(DATA, "projects")
    BACKUPS = os.path.join(DATA, "backups")
    DB_PATH = os.path.join(DATA, "jarvis.db")
    for p in (DATA, PROJECTS, BACKUPS):
        os.makedirs(p, exist_ok=True)
    q("""CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, role TEXT, text TEXT, ts TEXT)""")
    q("""CREATE TABLE IF NOT EXISTS prefs(key TEXT PRIMARY KEY, value TEXT)""")
    q("""CREATE TABLE IF NOT EXISTS projects(name TEXT PRIMARY KEY, info TEXT, ts TEXT)""")
    q("""CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, text TEXT, at TEXT)""")


def q(sql, args=(), fetch=False):
    c = sqlite3.connect(DB_PATH)
    try:
        cur = c.execute(sql, args)
        rows = cur.fetchall() if fetch else None
        c.commit()
        return rows
    finally:
        c.close()


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def pref_get(k, d=""):
    r = q("SELECT value FROM prefs WHERE key=?", (k,), True)
    return r[0][0] if r else d


def pref_set(k, v):
    q("INSERT OR REPLACE INTO prefs(key,value) VALUES(?,?)", (k, str(v)))


def msg_add(role, text):
    q("INSERT INTO messages(role,text,ts) VALUES(?,?,?)", (role, text, now()))


def msg_recent(n=30):
    return q("SELECT role,text,ts FROM messages ORDER BY id DESC LIMIT ?", (n,), True)[::-1]


def wipe(everything=False):
    q("DELETE FROM messages")
    q("DELETE FROM reminders")
    if everything:
        q("DELETE FROM prefs")
        q("DELETE FROM projects")


# ============================================================ 2. MEMORY (permission gated)
def mem_on():
    return pref_get("memory", "0") == "1"


def facts_list():
    try:
        return json.loads(pref_get("facts", "[]"))
    except Exception:
        return []


def remember(fact):
    if not mem_on():
        return "Memory abhi OFF hai. Settings mein Memory ON karein, tab main yaad rakhunga."
    f = facts_list()
    f.append(fact.strip())
    pref_set("facts", json.dumps(f[-50:], ensure_ascii=False))
    return "Theek hai, yaad rakh liya: " + fact.strip()


# ============================================================ 3. HTTP + AI + SEARCH
def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def http(url, data=None, headers=None, timeout=25):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 JARVIS-AI")
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        return r.read().decode("utf-8", "replace")


PROVIDERS = {
    "offline": (None, None),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "meta-llama/llama-3.3-70b-instruct:free"),
    "gemini": (None, "gemini-2.0-flash"),
}
SYSTEM = ("You are JARVIS, a helpful personal AI assistant. Reply in the same language the user uses "
          "(Hindi, English or Hinglish). Be concise and clear.")
SESSION = []  # in-memory conversation (last turns)


def ai_chat(messages):
    prov, key = pref_get("provider", "offline"), pref_get("api_key")
    if prov not in PROVIDERS or prov == "offline" or not key:
        raise RuntimeError("no_ai")
    url, model = PROVIDERS[prov]
    model = pref_get("model") or model
    if prov == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s" % (model, key)
        sysm = " ".join(m["content"] for m in messages if m["role"] == "system")
        contents = [{"role": "model" if m["role"] == "assistant" else "user",
                     "parts": [{"text": m["content"]}]} for m in messages if m["role"] != "system"]
        r = json.loads(http(url, {"contents": contents, "systemInstruction": {"parts": [{"text": sysm}]}}))
        return r["candidates"][0]["content"]["parts"][0]["text"]
    r = json.loads(http(url, {"model": model, "messages": messages}, {"Authorization": "Bearer " + key}))
    return r["choices"][0]["message"]["content"]


def ai_reply(_text=None):
    sysm = SYSTEM
    if mem_on() and facts_list():
        sysm += " Known facts about user: " + "; ".join(facts_list())
    return ai_chat([{"role": "system", "content": sysm}] + SESSION[-10:])


def offline_reply(t):
    l = t.lower()
    if any(w in l for w in ("hello", "hi", "hey", "namaste", "नमस्ते")):
        return "Namaste! Main JARVIS hoon. Battery, time, app kholna, search jaise commands bol sakte hain."
    return ("Offline mode: AI jawab ke liye Settings mein provider (groq/gemini/openrouter) aur API key daalein. "
            "Phone commands (battery, time, open app, search) bina key ke bhi chalte hain.")


def web_search(query, n=4):
    page = http("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', page, re.S)
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
    clean = lambda s: html.unescape(re.sub(r"<.*?>", "", s)).strip()
    return [(clean(t), clean(snips[i]) if i < len(snips) else "") for i, t in enumerate(titles[:n])]


def web_answer(query):
    res = web_search(query)
    if not res:
        return "Kuch result nahi mila."
    raw = "\n".join("- %s: %s" % r for r in res)
    try:
        return ai_chat([{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": "Question: %s\nWeb results:\n%s\nAnswer briefly." % (query, raw)}])
    except RuntimeError:
        return "Search results:\n" + raw


def is_hindi(s):
    return bool(re.search(r"[\u0900-\u097F]", s))


def strip_fence(s):
    m = re.search(r"```[a-zA-Z0-9]*\n(.*?)```", s, re.S)
    return (m.group(1) if m else s).strip("\n") + "\n"


def ai_code(prompt):
    return strip_fence(ai_chat([
        {"role": "system", "content": "You are a senior developer. Reply with ONE complete code block only."},
        {"role": "user", "content": prompt}]))


def ai_explain(code, errors):
    return ai_chat([{"role": "system", "content": "Explain these code errors simply (Hinglish ok) and suggest fixes."},
                    {"role": "user", "content": "Errors:\n%s\n\nCode:\n%s" % ("\n".join(errors), code)}])


def ai_fix(code, errors):
    return ai_code("Fix this code. Errors:\n%s\n\nCode:\n%s" % ("\n".join(errors), code))


# ============================================================ 4. ANDROID PHONE CONTROL
def _need_android():
    if not ANDROID:
        raise RuntimeError("Ye feature sirf Android phone (APK) par chalta hai.")


def _act():
    from jnius import autoclass
    return autoclass("org.kivy.android.PythonActivity").mActivity


def _start(action, uri=None):
    _need_android()
    from jnius import autoclass
    Intent = autoclass("android.content.Intent")
    i = Intent(action)
    if uri:
        i.setData(autoclass("android.net.Uri").parse(uri))
    i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    _act().startActivity(i)


def battery_info():
    _need_android()
    from jnius import autoclass
    Intent, IF = autoclass("android.content.Intent"), autoclass("android.content.IntentFilter")
    st = _act().registerReceiver(None, IF(Intent.ACTION_BATTERY_CHANGED))
    lvl, scale, plug = st.getIntExtra("level", -1), st.getIntExtra("scale", 100), st.getIntExtra("plugged", 0)
    return "Battery %d%% hai, %s." % (int(lvl * 100 / max(scale, 1)), "charging ho rahi hai" if plug else "charging nahi ho rahi")


def internet_ok():
    try:
        socket.create_connection(("8.8.8.8", 53), 3).close()
        return True
    except Exception:
        return False


def open_url(url):
    if not url.startswith("http"):
        url = "https://" + url
    if ANDROID:
        _start("android.intent.action.VIEW", url)
    else:
        import webbrowser
        webbrowser.open(url)
    return "Khol diya: " + url


def device_info():
    _need_android()
    from jnius import autoclass
    B, V = autoclass("android.os.Build"), autoclass("android.os.Build$VERSION")
    return "Device: %s %s | Android %s | SDK %s" % (B.MANUFACTURER, B.MODEL, V.RELEASE, V.SDK_INT)


def open_app(name):
    _need_android()
    pm = _act().getPackageManager()
    apps, best = pm.getInstalledApplications(0), None
    for i in range(apps.size()):
        a = apps.get(i)
        label = str(pm.getApplicationLabel(a).toString()).lower()
        if label == name or (name in label and best is None):
            if pm.getLaunchIntentForPackage(a.packageName) is not None:
                best = (label, a.packageName)
                if label == name:
                    break
    if not best:
        return "'%s' naam ka app nahi mila." % name
    it = pm.getLaunchIntentForPackage(best[1])
    it.addFlags(268435456)  # FLAG_ACTIVITY_NEW_TASK
    _act().startActivity(it)
    return "%s khol diya." % best[0]


def parse_time(t):
    m = re.search(r"(\d{1,2})[:.](\d{2})\s*(am|pm)?", t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        m = re.search(r"(\d{1,2})\s*(am|pm|baje|बजे)", t)
        if not m:
            return None
        h, mi, ap = int(m.group(1)), 0, m.group(2)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return (h, mi) if h < 24 and mi < 60 else None


def set_reminder(text):
    tm = parse_time(text.lower())
    if not tm:
        return "Time samajh nahi aaya. Aise bolein: 'remind me at 7:30 pm medicine'."
    q("INSERT INTO reminders(text,at) VALUES(?,?)", (text, "%02d:%02d" % tm))
    _need_android()
    from jnius import autoclass
    Intent = autoclass("android.content.Intent")
    i = Intent("android.intent.action.SET_ALARM")
    i.putExtra("android.intent.extra.alarm.HOUR", tm[0])
    i.putExtra("android.intent.extra.alarm.MINUTES", tm[1])
    i.putExtra("android.intent.extra.alarm.MESSAGE", "JARVIS: " + text[:60])
    i.putExtra("android.intent.extra.alarm.SKIP_UI", False)
    i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    _act().startActivity(i)
    return "Reminder %02d:%02d ke liye alarm app khol diya. Wahan confirm karein." % tm


def route(text):
    """Returns None (not a command) or (reply, confirm_fn). If confirm_fn is set, UI asks YES/NO first."""
    t = text.lower().strip()
    short = len(t.split()) <= 9
    has = lambda *w: any(x in t for x in w)
    try:
        m = re.match(r"^(remember|yaad rakh(o)?)\s+(.+)", t)
        if m:
            return (remember(m.group(3)), None)
        if has("remind", "alarm", "yaad dila"):
            return (set_reminder(text), None)
        m = re.match(r"^(search|google|khojo|dhundo)\s+(.+)", t)
        if m:
            return (web_answer(m.group(2)), None)
        if has("call ", "phone lagao", "dial "):
            n = re.search(r"\+?\d[\d ]{5,}", t)
            if not n:
                return ("Kis number par call karun? Number ke saath bolein.", None)
            num = n.group(0).replace(" ", "")
            return ("Dialer mein %s kholun?" % num, lambda: (_start("android.intent.action.DIAL", "tel:" + num), "Dialer khol diya.")[1])
        m = re.match(r"^(open|launch|start)\s+(.+)", t) or re.match(r"^(.+?)\s+(kholo|khol do|खोलो|खोल दो)$", t)
        if m and short:
            target = (m.group(2) if m.group(1) in ("open", "launch", "start") else m.group(1)).strip()
            if "camera" in target:
                _start("android.media.action.IMAGE_CAPTURE")
                return ("Camera khol diya.", None)
            if target in ("settings", "setting"):
                _start("android.settings.SETTINGS")
                return ("Settings khol di.", None)
            if "." in target or target.startswith("www"):
                return ("Website '%s' kholun?" % target, lambda: open_url(target))
            return (open_app(target), None)
        if short:
            if has("battery", "बैटरी"):
                return (battery_info(), None)
            if has("internet", "wifi", "इंटरनेट"):
                return ("Internet chal raha hai." if internet_ok() else "Internet nahi hai.", None)
            if has("device info", "phone info", "device information"):
                return (device_info(), None)
            if has("what time", "kitne baje", "samay", "समय", "कितने बजे", "date", "tareekh", "तारीख", "today's date"):
                return (datetime.datetime.now().strftime("Abhi %I:%M %p hai, %A, %d %B %Y."), None)
            if has("camera"):
                _start("android.media.action.IMAGE_CAPTURE")
                return ("Camera khol diya.", None)
            if has("settings"):
                _start("android.settings.SETTINGS")
                return ("Settings khol di.", None)
    except Exception as e:
        return (str(e), None)
    return None


# ============================================================ 5. VOICE (STT + TTS)
class TTS:
    def __init__(self):
        self.tts, self.lang = None, None

    def init(self):
        if not ANDROID or self.tts:
            return
        try:
            from jnius import autoclass
            self.tts = autoclass("android.speech.tts.TextToSpeech")(_act(), None)
        except Exception as e:
            print("TTS init failed:", e)

    def speak(self, text):
        if not self.tts:
            return
        try:
            from jnius import autoclass
            loc = autoclass("java.util.Locale")("hi", "IN") if is_hindi(text) else autoclass("java.util.Locale")("en", "IN")
            if self.lang != loc.toString():
                self.tts.setLanguage(loc)
                self.lang = loc.toString()
            self.tts.speak(text[:3500], 0, None, "jarvis")
        except Exception as e:
            print("TTS error:", e)

    def speaking(self):
        try:
            return bool(self.tts and self.tts.isSpeaking())
        except Exception:
            return False

    def stop(self):
        try:
            self.tts and self.tts.stop()
        except Exception:
            pass


class STT:
    CODE = 4242

    def listen(self, lang, cb):
        if not ANDROID:
            cb(None, "Voice input sirf Android phone par chalta hai.")
            return
        try:
            from jnius import autoclass
            from android import activity
            Intent, RI = autoclass("android.content.Intent"), autoclass("android.speech.RecognizerIntent")
            it = Intent(RI.ACTION_RECOGNIZE_SPEECH)
            it.putExtra(RI.EXTRA_LANGUAGE_MODEL, RI.LANGUAGE_MODEL_FREE_FORM)
            it.putExtra(RI.EXTRA_LANGUAGE, lang)
            it.putExtra(RI.EXTRA_PROMPT, "JARVIS sun raha hai...")
            self.cb = cb
            activity.bind(on_activity_result=self._res)
            _act().startActivityForResult(it, self.CODE)
        except Exception as e:
            cb(None, "Voice error: %s" % e)

    def _res(self, req, res, data):
        if req != self.CODE:
            return
        from android import activity
        activity.unbind(on_activity_result=self._res)
        text = None
        if res == -1 and data is not None:
            from jnius import autoclass
            lst = data.getStringArrayListExtra(autoclass("android.speech.RecognizerIntent").EXTRA_RESULTS)
            if lst is not None and lst.size() > 0:
                text = str(lst.get(0))
        self.cb(text, None if text else "Kuch sunai nahi diya.")


# ============================================================ 6. DEVELOPER TOOLS (files, backups)
def slug(name):
    s = re.sub(r"[^A-Za-z0-9_\-]", "_", name.strip())[:40]
    if not s:
        raise ValueError("Naam khali hai")
    return s


def safe_path(project, rel=""):
    root = os.path.realpath(os.path.join(PROJECTS, project))
    p = os.path.realpath(os.path.join(root, rel))
    if not (p == root or p.startswith(root + os.sep)):
        raise PermissionError("Path project ke bahar hai - blocked")
    return p


def create_project(name):
    name = slug(name)
    root = safe_path(name)
    os.makedirs(root, exist_ok=True)
    mp = os.path.join(root, "main.py")
    if not os.path.exists(mp):
        with open(mp, "w", encoding="utf-8") as f:
            f.write('print("Hello from %s")\n' % name)
    q("INSERT OR REPLACE INTO projects(name,info,ts) VALUES(?,?,?)", (name, "", now()))
    return name


def list_projects():
    return sorted(d for d in os.listdir(PROJECTS) if os.path.isdir(os.path.join(PROJECTS, d)))


def list_files(project):
    root, out = safe_path(project), []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith(".") and d not in ("bin", "__pycache__")]
        for f in fn:
            out.append(os.path.relpath(os.path.join(dp, f), root))
    return sorted(out)[:300]


def read_file(project, rel):
    with open(safe_path(project, rel), "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def write_file(project, rel, content):
    p = safe_path(project, rel)
    if os.path.exists(p):  # automatic backup before every edit
        bd = os.path.join(BACKUPS, project)
        os.makedirs(bd, exist_ok=True)
        shutil.copy2(p, os.path.join(bd, "%s.%s.bak" % (rel.replace(os.sep, "_"), datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


def backup_project(project):
    base = os.path.join(BACKUPS, "%s_%s" % (project, datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
    return shutil.make_archive(base, "zip", safe_path(project))


# ============================================================ 7. CODE RUNNER + TESTER
SAFE_MODULES = {"math", "random", "time", "datetime", "json", "re", "string", "itertools", "collections",
                "statistics", "functools", "heapq", "bisect", "decimal", "fractions", "textwrap"}
SAFE_BUILTINS = ("abs all any bool bytes chr dict divmod enumerate filter float format frozenset hasattr int "
                 "isinstance issubclass iter len list map max min next object ord pow range repr reversed round "
                 "set slice sorted str sum tuple type zip callable property staticmethod classmethod super "
                 "Exception ValueError TypeError KeyError IndexError ZeroDivisionError AssertionError "
                 "StopIteration RuntimeError AttributeError NameError ImportError PermissionError "
                 "True False None __build_class__").split()


def find_errors(code, filename="main.py"):
    if not filename.endswith(".py"):
        return []
    try:
        ast.parse(code)
        return []
    except SyntaxError as e:
        return ["Line %s: %s" % (e.lineno, e.msg)]


def _sandbox_exec(code, project, tests=False):
    import builtins
    out = []
    size = [0]

    def _print(*a, sep=" ", end="\n", **k):
        s = sep.join(str(x) for x in a) + end
        size[0] += len(s)
        if size[0] < 200000:
            out.append(s)

    def _import(name, g=None, l=None, fromlist=(), level=0):
        if name.split(".")[0] not in SAFE_MODULES:
            raise ImportError("Blocked import: " + name)
        return __import__(name, g, l, fromlist, level)

    def _open(path, mode="r", *a, **k):
        return open(safe_path(project, path), mode, *a, **k)

    b = {n: getattr(builtins, n) for n in SAFE_BUILTINS if hasattr(builtins, n)}
    b.update({"print": _print, "__import__": _import, "open": _open})
    g = {"__builtins__": b, "__name__": "__main__"}
    try:
        exec(compile(code, "<project>", "exec"), g)
        if tests:
            names = [n for n, f in g.items() if n.startswith("test_") and callable(f)]
            if not names:
                out.append("(no test_ functions found)\n")
            failed = 0
            for n in names:
                try:
                    g[n]()
                    out.append("PASS %s\n" % n)
                except BaseException as e:
                    failed += 1
                    out.append("FAIL %s: %s\n" % (n, e))
            return (failed == 0), "".join(out)
        return True, "".join(out)
    except KeyboardInterrupt:
        return False, "".join(out) + "\n[STOPPED]"
    except BaseException:
        return False, "".join(out) + "\n" + traceback.format_exc(limit=-2)


class Runner:
    def __init__(self):
        self.th, self.timer = None, None

    def busy(self):
        return self.th is not None and self.th.is_alive()

    def start(self, code, project, timeout=10, tests=False, done=None):
        if self.busy():
            return False

        def work():
            res = _sandbox_exec(code, project, tests)
            if self.timer:
                self.timer.cancel()
            if done:
                done(*res)

        self.th = threading.Thread(target=work, daemon=True)
        self.th.start()
        self.timer = threading.Timer(timeout, self.stop)
        self.timer.daemon = True
        self.timer.start()
        return True

    def stop(self):
        th = self.th
        if th and th.is_alive():
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(th.ident), ctypes.py_object(KeyboardInterrupt))


def run_code(code, project, timeout=10, tests=False):
    box, ev = [], threading.Event()
    Runner().start(code, project, timeout, tests, lambda ok, out: (box.append((ok, out)), ev.set()))
    ev.wait(timeout + 3)
    return box[0] if box else (False, "Timeout")


# ============================================================ 8. APK BUILD FILES
JARVIS_REQS = "python3,kivy==2.3.0,pyjnius,android,sqlite3,openssl,certifi"
WORKFLOW = """name: Build APK
on: [push, workflow_dispatch]
jobs:
  build:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - name: Download Hindi-capable font (optional)
        run: |
          mkdir -p assets
          [ -f assets/font.ttf ] || curl -fL -o assets/font.ttf https://github.com/google/fonts/raw/main/ofl/mukta/Mukta-Regular.ttf || echo "font download skipped"
      - name: Build APK with Buildozer
        id: buildozer
        uses: ArtemSBulgakov/buildozer-action@v1
        with:
          command: buildozer android debug
          buildozer_version: stable
      - name: Upload APK
        uses: actions/upload-artifact@v4
        with:
          name: apk
          path: ${{ steps.buildozer.outputs.filename }}
"""


def spec_text(title, package_id, version, reqs):
    domain, _, name = package_id.rpartition(".")
    domain = domain or "org.app"
    return """[app]
title = %s
package.name = %s
package.domain = %s
source.dir = .
source.include_exts = py,png,jpg,kv,ttf,json
source.exclude_dirs = .github,bin,.buildozer,__pycache__
version = %s
requirements = %s
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,ACCESS_NETWORK_STATE,RECORD_AUDIO,QUERY_ALL_PACKAGES,com.android.alarm.permission.SET_ALARM
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = False

[buildozer]
log_level = 2
warn_on_root = 1
""" % (title, re.sub(r"[^a-z0-9]", "", name.lower()) or "app", domain, version, reqs)


def write_build_files(project, title, package_id, version):
    reqs = "python3,kivy==2.3.0"
    write_file(project, "buildozer.spec", spec_text(title, package_id, version, reqs))
    write_file(project, ".github/workflows/build-apk.yml", WORKFLOW)


def gh_status(repo):
    d = json.loads(http("https://api.github.com/repos/%s/actions/runs?per_page=1" % repo))
    if not d.get("workflow_runs"):
        return "Koi build run nahi mila.", ""
    r = d["workflow_runs"][0]
    return "Status: %s | Result: %s" % (r["status"], r["conclusion"]), r["html_url"]


def init_build_files():
    with open("buildozer.spec", "w") as f:
        f.write(spec_text("JARVIS AI", "org.jarvis.jarvisai", "1.0.0", JARVIS_REQS))
    os.makedirs(".github/workflows", exist_ok=True)
    with open(".github/workflows/build-apk.yml", "w") as f:
        f.write(WORKFLOW)
    print("Created: buildozer.spec, .github/workflows/build-apk.yml")


# ============================================================ 9. SELF TEST (no Kivy needed)
def selftest():
    import tempfile
    init_paths(tempfile.mkdtemp())
    res = []

    def chk(name, cond):
        print(("PASS  " if cond else "FAIL  ") + name)
        res.append(bool(cond))

    pref_set("x", "1"); chk("database prefs", pref_get("x") == "1")
    msg_add("user", "hi"); chk("database messages", len(msg_recent(5)) == 1)
    pref_set("memory", "0"); chk("memory blocked when OFF", "OFF" in remember("a"))
    pref_set("memory", "1"); remember("my name is Raj"); chk("memory saves when ON", facts_list() == ["my name is Raj"])
    chk("route: time", route("what time is it") and "Abhi" in route("what time is it")[0])
    chk("route: unknown text goes to AI", route("explain gravity in detail please for me now") is None)
    chk("route: reminder parse", parse_time("remind me at 7:30 pm") == (19, 30))
    chk("offline reply", "Offline" in offline_reply("xyz"))
    p = create_project("demo"); chk("create project", p == "demo" and "main.py" in list_files("demo"))
    write_file("demo", "main.py", "print('v2')\n"); chk("backup before edit", os.listdir(os.path.join(BACKUPS, "demo")) != [])
    try:
        safe_path("demo", "../../etc/passwd"); chk("path traversal blocked", False)
    except PermissionError:
        chk("path traversal blocked", True)
    chk("project zip backup", os.path.exists(backup_project("demo")))
    chk("find_errors detects syntax error", len(find_errors("def f(:\n  pass")) == 1)
    chk("find_errors clean code", find_errors("print(1)") == [])
    ok, out = run_code("print(1+1)", "demo"); chk("run code ok", ok and out == "2\n")
    ok, out = run_code("import os\nos.system('ls')", "demo"); chk("blocked import", (not ok) and "Blocked" in out)
    ok, out = run_code("while True: pass", "demo", timeout=1); chk("timeout stops infinite loop", (not ok) and "STOPPED" in out)
    ok, out = run_code("def test_a():\n assert 1==1\ndef test_b():\n assert 1==2", "demo", tests=True)
    chk("test runner pass+fail", (not ok) and "PASS test_a" in out and "FAIL test_b" in out)
    chk("strip_fence", strip_fence("x\n```python\nprint(1)\n```") == "print(1)\n")
    chk("spec has package", "package.name = myapp" in spec_text("T", "org.x.myapp", "1.0", "python3,kivy"))
    print("\n%d/%d passed" % (sum(res), len(res)))
    sys.exit(0 if all(res) else 1)


# ============================================================ 10. UI (Kivy)
def run_ui():
    from kivy.app import App
    from kivy.core.window import Window
    from kivy.clock import Clock
    from kivy.metrics import dp
    from kivy.core.text import LabelBase
    from kivy.uix.screenmanager import ScreenManager, Screen, NoTransition
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.gridlayout import GridLayout
    from kivy.uix.label import Label
    from kivy.uix.button import Button
    from kivy.uix.textinput import TextInput
    from kivy.uix.scrollview import ScrollView
    from kivy.uix.popup import Popup
    from kivy.uix.spinner import Spinner
    from kivy.uix.switch import Switch
    from kivy.uix.widget import Widget
    from kivy.graphics import Color, Ellipse, Line, Rectangle

    CYAN = (0.0, 0.85, 1.0, 1)
    DARK = (0.0, 0.1, 0.18, 1)

    # Devanagari font: assets/font.ttf (downloaded by the workflow) or a system font
    for fp in (os.path.join(BASE, "assets", "font.ttf"), "/system/fonts/NotoSansDevanagari-Regular.ttf",
               "/system/fonts/NotoSansDevanagariUI-Regular.ttf"):
        if os.path.exists(fp) and os.path.getsize(fp) > 1000:
            LabelBase.register(name="Roboto", fn_regular=fp)
            break

    APP = App.get_running_app

    class NeonButton(Button):
        def __init__(self, **kw):
            kw.setdefault("background_normal", "")
            kw.setdefault("background_color", DARK)
            kw.setdefault("color", CYAN)
            kw.setdefault("size_hint_y", None)
            kw.setdefault("height", dp(44))
            super().__init__(**kw)
            with self.canvas.after:
                Color(0, 0.8, 1, 0.8)
                self._ln = Line(rectangle=(0, 0, 0, 0), width=1.1)
            self.bind(pos=self._u, size=self._u)

        def _u(self, *a):
            self._ln.rectangle = (self.x, self.y, self.width, self.height)

    def TI(**kw):
        kw.setdefault("background_color", (0.02, 0.08, 0.12, 1))
        kw.setdefault("foreground_color", (0.85, 0.97, 1, 1))
        kw.setdefault("cursor_color", CYAN)
        kw.setdefault("hint_text_color", (0.3, 0.5, 0.6, 1))
        return TextInput(**kw)

    def lab(text="", h=30, **kw):
        kw.setdefault("color", CYAN)
        kw.setdefault("halign", "left")
        l = Label(text=text, size_hint_y=None, height=dp(h), **kw)
        l.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        return l

    def spin(text, values):
        return Spinner(text=text, values=values, size_hint_y=None, height=dp(44), background_normal="",
                       background_color=DARK, color=CYAN)

    def vbox():
        b = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8), padding=dp(10))
        b.bind(minimum_height=b.setter("height"))
        return b

    def popup(title, content, size=(0.92, 0.6)):
        return Popup(title=title, content=content, size_hint=size, auto_dismiss=False, title_color=CYAN,
                     background_color=(0, 0.05, 0.1, 1), separator_color=CYAN)

    def show_text(title, text):
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
        box.add_widget(TI(text=text, readonly=True))
        p = popup(title, box, (0.95, 0.8))
        b = NeonButton(text="CLOSE", on_release=lambda *_: p.dismiss())
        box.add_widget(b)
        p.open()

    def ask_text(title, hint, cb, multiline=False):
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
        t = TI(hint_text=hint, multiline=multiline)
        box.add_widget(t)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        p = popup(title, box, (0.92, 0.7 if multiline else 0.4))
        row.add_widget(NeonButton(text="CANCEL", on_release=lambda *_: p.dismiss()))
        row.add_widget(NeonButton(text="OK", on_release=lambda *_: (p.dismiss(), cb(t.text.strip()))))
        box.add_widget(row)
        p.open()

    def bubble(text, user):
        l = Label(text=("YOU: " if user else "JARVIS: ") + text, size_hint_y=None, halign="left", valign="top",
                  color=(0.7, 0.95, 1, 1) if user else (0.6, 1, 0.8, 1))
        l.bind(width=lambda w, v: setattr(w, "text_size", (v - dp(16), None)))
        l.bind(texture_size=lambda w, v: setattr(w, "height", v[1] + dp(14)))
        return l

    # ---------- animated widgets
    class Core(Widget):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.t = 0
            Clock.schedule_interval(self.tick, 1 / 30)

        def tick(self, dt):
            self.t += dt
            a = APP()
            amp = 1.0 if (a and a.active) else 0.25
            cx, cy = self.center
            r = min(self.size) / 2 * 0.9
            self.canvas.clear()
            with self.canvas:
                Color(0, 0.5, 1, 0.12)
                Ellipse(pos=(cx - r, cy - r), size=(2 * r, 2 * r))
                Color(0, 0.8, 1, 1)
                Line(circle=(cx, cy, r), width=1.5)
                Line(circle=(cx, cy, r * 0.82, self.t * 70 * amp, self.t * 70 * amp + 250), width=3)
                Color(0.3, 0.95, 1, 0.9)
                Line(circle=(cx, cy, r * 0.62, -self.t * 110 * amp, -self.t * 110 * amp + 200), width=2)
                pr = r * 0.3 * (1 + 0.18 * amp * math.sin(self.t * 6))
                Color(0, 0.9, 1, 0.9)
                Ellipse(pos=(cx - pr, cy - pr), size=(2 * pr, 2 * pr))

    class Waves(Widget):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.t = 0
            Clock.schedule_interval(self.tick, 1 / 24)

        def tick(self, dt):
            self.t += dt
            a = APP()
            amp = 1.0 if (a and a.active) else 0.08
            n, w = 28, self.width / 28
            self.canvas.clear()
            with self.canvas:
                Color(0, 0.8, 1, 0.9)
                for i in range(n):
                    h = self.height * (0.1 + 0.9 * amp * abs(math.sin(self.t * 5 + i * 0.6)))
                    Rectangle(pos=(self.x + i * w + 2, self.center_y - h / 2), size=(w - 4, h))

    # ---------- screens
    class HomeScreen(Screen):
        def __init__(self, **kw):
            super().__init__(**kw)
            b = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
            b.add_widget(lab("J.A.R.V.I.S", 40, font_size=dp(26), halign="center", bold=True))
            b.add_widget(Core())
            b.add_widget(Waves(size_hint_y=None, height=dp(50)))
            self.status = lab("Ready", 28, halign="center")
            self.last = Label(text="", size_hint_y=None, height=dp(90), color=(0.7, 1, 0.9, 1), halign="center", max_lines=4)
            self.last.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
            self.mic = NeonButton(text="MIC  -  TAP TO SPEAK", height=dp(64), font_size=dp(18))
            self.mic.bind(on_release=lambda *_: APP().listen())
            for w in (self.status, self.last, self.mic):
                b.add_widget(w)
            self.add_widget(b)

    class ChatScreen(Screen):
        def __init__(self, **kw):
            super().__init__(**kw)
            b = BoxLayout(orientation="vertical", padding=dp(6), spacing=dp(6))
            self.sv = ScrollView()
            self.msgs = vbox()
            self.sv.add_widget(self.msgs)
            row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
            self.inp = TI(hint_text="Type here / yahan likhein", multiline=False)
            self.inp.bind(on_text_validate=lambda *_: self.send())
            row.add_widget(self.inp)
            row.add_widget(NeonButton(text="SEND", size_hint_x=None, width=dp(70), on_release=lambda *_: self.send()))
            row.add_widget(NeonButton(text="MIC", size_hint_x=None, width=dp(60), on_release=lambda *_: APP().listen()))
            b.add_widget(self.sv)
            b.add_widget(row)
            self.add_widget(b)

        def send(self):
            t, self.inp.text = self.inp.text, ""
            APP().user_input(t)

        def add(self, text, user):
            self.msgs.add_widget(bubble(text, user))
            Clock.schedule_once(lambda dt: setattr(self.sv, "scroll_y", 0), 0.15)

    class DevScreen(Screen):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.project, self.file, self.runner = "", "", Runner()
            sv, box = ScrollView(), vbox()
            sv.add_widget(box)
            self.psp, self.fsp = spin("(select project)", []), spin("(select file)", [])
            self.psp.bind(text=lambda s, t: t in list_projects() and self.open_project())
            self.fsp.bind(text=lambda s, t: self.project and t in list_files(self.project) and self.load_file(t))
            self.editor = TI(size_hint_y=None, height=dp(260), font_size=dp(13), multiline=True)
            self.status = lab("STATUS: idle", 32, bold=True)
            self.out = TI(size_hint_y=None, height=dp(200), font_size=dp(12), readonly=True, hint_text="Terminal / errors / tests / logs")
            grid = GridLayout(cols=3, size_hint_y=None, spacing=dp(6), row_default_height=dp(44), row_force_default=True)
            grid.bind(minimum_height=grid.setter("height"))
            for t, f in [("RUN", self.run), ("STOP", self.stop), ("TEST", self.test), ("FIND ERR", self.find),
                         ("FIX ERR", self.fix), ("SAVE", self.save), ("NEW PROJECT", self.new_project),
                         ("OPEN PROJECT", self.open_project), ("NEW FILE", self.new_file),
                         ("BACKUP", self.backup), ("AI CODE", self.aigen)]:
                grid.add_widget(NeonButton(text=t, font_size=dp(12), on_release=lambda b, f=f: f()))
            for w in (lab("DEVELOPER MODE", 30, bold=True), self.psp, self.fsp, self.editor, grid, self.status, self.out):
                box.add_widget(w)
            self.add_widget(sv)

        def on_pre_enter(self, *a):
            self.psp.values = list_projects()

        def log(self, t, append=False):
            self.out.text = (self.out.text + "\n" + t) if append else t

        def set_status(self, s, ok=None):
            self.status.text = "STATUS: " + s
            self.status.color = CYAN if ok is None else ((0.2, 1, 0.4, 1) if ok else (1, 0.3, 0.3, 1))

        def need(self):
            if not self.project or not self.file:
                self.set_status("project/file chunein", False)
                return False
            return True

        def new_project(self):
            def mk(n):
                try:
                    self.psp.values = list_projects() + [create_project(n)]
                    self.psp.text = slug(n)
                except Exception as e:
                    self.log("Error: %s" % e)
            ask_text("New project", "project name", mk)

        def open_project(self):
            self.project = self.psp.text
            if self.project not in list_projects():
                return
            files = list_files(self.project)
            self.fsp.values = files
            self.fsp.text = "main.py" if "main.py" in files else (files[0] if files else "(select file)")
            if self.fsp.text in files:
                self.load_file(self.fsp.text)

        def load_file(self, f):
            self.file = f
            self.editor.text = read_file(self.project, f)
            self.set_status("opened " + f)

        def new_file(self):
            if not self.project:
                return self.set_status("pehle project chunein", False)

            def mk(n):
                try:
                    write_file(self.project, n, "")
                    self.fsp.values = list_files(self.project)
                    self.fsp.text = n
                except Exception as e:
                    self.log("Error: %s" % e)
            ask_text("New file", "e.g. app.py or index.html", mk)

        def save(self):
            if self.need():
                write_file(self.project, self.file, self.editor.text)
                self.set_status("saved (backup made)", True)

        def backup(self):
            if self.project:
                self.log("Backup: " + backup_project(self.project))

        def _done(self, ok, out):
            Clock.schedule_once(lambda dt: (self.log(out or "(no output)"), self.set_status("SUCCESS" if ok else "FAILED", ok)))

        def _run(self, tests):
            if not self.need():
                return
            code = self.editor.text

            def go():
                self.set_status("running...")
                if not self.runner.start(code, self.project, 10, tests, self._done):
                    self.set_status("already running", False)
            APP().confirm("%s '%s' in restricted mode (10s limit)?" % ("Test" if tests else "Run", self.file), go, thread=False)

        def run(self):
            self._run(False)

        def test(self):
            self._run(True)

        def stop(self):
            self.runner.stop()
            self.set_status("stop requested")

        def find(self):
            if not self.need():
                return
            errs = find_errors(self.editor.text, self.file)
            if not errs:
                self.log("No syntax errors found." if self.file.endswith(".py") else "Only Python files are checked.")
                return self.set_status("no errors", True)
            self.log("\n".join(errs))
            self.set_status("errors found", False)

            def work():
                try:
                    ex = ai_explain(self.editor.text, errs)
                except Exception:
                    ex = "(AI explain ke liye Settings mein API key daalein)"
                Clock.schedule_once(lambda dt: self.log("\n" + ex, True))
            threading.Thread(target=work, daemon=True).start()

        def fix(self):
            if not self.need():
                return
            errs = find_errors(self.editor.text, self.file) or ["Improve and fix any problems"]
            self.set_status("AI fix soch raha hai...")

            def work():
                try:
                    new = ai_fix(self.editor.text, errs)
                except Exception as e:
                    return Clock.schedule_once(lambda dt: self.log("Fix error: %s" % e))
                Clock.schedule_once(lambda dt: self._preview(new))
            threading.Thread(target=work, daemon=True).start()

        def _preview(self, new):
            box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
            box.add_widget(TI(text=new, readonly=True))
            p = popup("Proposed fix - apply?", box, (0.95, 0.85))
            row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
            row.add_widget(NeonButton(text="REJECT", on_release=lambda *_: p.dismiss()))
            row.add_widget(NeonButton(text="APPLY + SAVE", on_release=lambda *_: (p.dismiss(), setattr(self.editor, "text", new), self.save())))
            box.add_widget(row)
            p.open()

        def aigen(self):
            def go(prompt):
                if not prompt:
                    return
                self.set_status("AI code bana raha hai...")

                def work():
                    try:
                        c = ai_code(prompt)
                        Clock.schedule_once(lambda dt: (setattr(self.editor, "text", c), self.set_status("code generated - review & SAVE", True)))
                    except Exception as e:
                        Clock.schedule_once(lambda dt: (self.log("AI error: %s" % e), self.set_status("AI failed", False)))
                threading.Thread(target=work, daemon=True).start()
            ask_text("AI code", "kya banana hai? (e.g. Python calculator)", go, True)

    class ApkScreen(Screen):
        def __init__(self, **kw):
            super().__init__(**kw)
            sv, box = ScrollView(), vbox()
            sv.add_widget(box)
            self.psp = spin("(select project)", [])
            self.name = TI(text="My App", multiline=False, size_hint_y=None, height=dp(44))
            self.pkg = TI(text="org.jarvis.myapp", multiline=False, size_hint_y=None, height=dp(44))
            self.ver = TI(text="1.0.0", multiline=False, size_hint_y=None, height=dp(44))
            self.repo = TI(text=pref_get("repo"), hint_text="GitHub repo: username/reponame", multiline=False, size_hint_y=None, height=dp(44))
            self.out = TI(size_hint_y=None, height=dp(260), readonly=True, font_size=dp(12))
            self.url = ""
            self.out.text = ("1) Project chunein, naam/package/version bharein\n2) GENERATE BUILD FILES dabayein\n"
                             "3) Project GitHub par push karein (README dekhein)\n4) CHECK BUILD se status dekhein\n"
                             "5) Build green ho to GitHub > Actions > run > Artifacts se APK download karein.\n"
                             "Note: APK phone par nahi banta; build GitHub ke cloud Linux par hota hai.")
            for w in (lab("APK BUILDER", 30, bold=True), self.psp, lab("App name"), self.name, lab("Package ID"), self.pkg,
                      lab("Version"), self.ver, lab("GitHub repo"), self.repo,
                      NeonButton(text="GENERATE BUILD FILES", on_release=lambda *_: self.gen()),
                      NeonButton(text="CHECK CLOUD BUILD STATUS", on_release=lambda *_: self.check()),
                      NeonButton(text="OPEN BUILD PAGE (download APK)", on_release=lambda *_: self.url and open_url(self.url)),
                      self.out):
                box.add_widget(w)
            self.add_widget(sv)

        def on_pre_enter(self, *a):
            self.psp.values = list_projects()

        def gen(self):
            if self.psp.text not in list_projects():
                self.out.text = "Pehle project chunein."
                return
            try:
                write_build_files(self.psp.text, self.name.text.strip(), self.pkg.text.strip(), self.ver.text.strip())
                self.out.text = ("buildozer.spec aur .github/workflows/build-apk.yml project mein ban gaye.\n"
                                 "Ab project folder GitHub par push karein.")
            except Exception as e:
                self.out.text = "Error: %s" % e

        def check(self):
            repo = self.repo.text.strip()
            pref_set("repo", repo)
            self.out.text = "Checking..."

            def work():
                try:
                    s, u = gh_status(repo)
                    self.url = u
                    Clock.schedule_once(lambda dt: setattr(self.out, "text", s + "\n" + u))
                except Exception as e:
                    Clock.schedule_once(lambda dt: setattr(self.out, "text", "Error (repo public hai? naam sahi hai?): %s" % e))
            threading.Thread(target=work, daemon=True).start()

    class SettingsScreen(Screen):
        def __init__(self, **kw):
            super().__init__(**kw)
            sv, box = ScrollView(), vbox()
            sv.add_widget(box)
            self.prov = spin(pref_get("provider", "offline"), list(PROVIDERS))
            self.model = TI(text=pref_get("model"), hint_text="model (optional)", multiline=False, size_hint_y=None, height=dp(44))
            self.key = TI(text=pref_get("api_key"), hint_text="API key", password=True, multiline=False, size_hint_y=None, height=dp(44))
            self.lang = spin(pref_get("lang", "en-IN"), ["en-IN", "hi-IN"])
            self.speak, self.mem = Switch(active=pref_get("speak", "1") == "1"), Switch(active=mem_on())
            for t, sw in (("Speak replies", self.speak), ("Long-term memory (permission)", self.mem)):
                r = BoxLayout(size_hint_y=None, height=dp(44))
                r.add_widget(lab(t))
                r.add_widget(sw)
                box.add_widget(r)
            for w in (lab("SETTINGS", 30, bold=True), lab("AI provider"), self.prov, lab("Model"), self.model,
                      lab("API key (sirf is phone mein private store)"), self.key, lab("Voice language"), self.lang):
                box.add_widget(w)
            for t, f in [("SAVE SETTINGS", self.save), ("VIEW HISTORY", self.hist), ("VIEW MEMORY FACTS", self.facts),
                         ("CLEAR HISTORY", lambda: APP().confirm("History delete karein?", lambda: wipe(False), thread=False)),
                         ("DELETE ALL DATA", lambda: APP().confirm("SAB data (settings + key bhi) delete karein?", lambda: wipe(True), thread=False))]:
                box.add_widget(NeonButton(text=t, on_release=lambda b, f=f: f()))
            self.add_widget(sv)

        def save(self):
            for k, v in (("provider", self.prov.text), ("model", self.model.text.strip()), ("api_key", self.key.text.strip()),
                         ("lang", self.lang.text), ("speak", "1" if self.speak.active else "0"), ("memory", "1" if self.mem.active else "0")):
                pref_set(k, v)
            APP().toast("Settings saved")

        def hist(self):
            show_text("History", "\n".join("[%s] %s: %s" % (ts, r, t) for r, t, ts in msg_recent(40)) or "(empty)")

        def facts(self):
            show_text("Memory facts", "\n".join(facts_list()) or "(empty)")

    # ---------- app
    class JarvisApp(App):
        title = "JARVIS AI"
        active = False

        def build(self):
            init_paths(self.user_data_dir)
            Window.clearcolor = (0, 0.01, 0.03, 1)
            Window.softinput_mode = "below_target"
            self.tts, self.stt, self.listening, self.busy = TTS(), STT(), False, False
            root = BoxLayout(orientation="vertical")
            self.sm = ScreenManager(transition=NoTransition())
            self.home, self.chat = HomeScreen(name="home"), ChatScreen(name="chat")
            for s in (self.home, self.chat, DevScreen(name="dev"), ApkScreen(name="apk"), SettingsScreen(name="settings")):
                self.sm.add_widget(s)
            nav = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(2))
            for n, t in (("home", "HOME"), ("chat", "CHAT"), ("dev", "DEV"), ("apk", "APK"), ("settings", "SET")):
                nav.add_widget(NeonButton(text=t, height=dp(50), on_release=lambda b, n=n: setattr(self.sm, "current", n)))
            root.add_widget(self.sm)
            root.add_widget(nav)
            Clock.schedule_interval(self._poll, 0.4)
            return root

        def on_start(self):
            if ANDROID:
                try:
                    from android.permissions import request_permissions
                    request_permissions(["android.permission.RECORD_AUDIO"])
                except Exception as e:
                    print("perm:", e)
            self.tts.init()
            self.bot_say("JARVIS online. Boliye, main kaise madad karun?", speak=False)

        def _poll(self, dt):
            self.active = self.listening or self.busy or self.tts.speaking()
            self.home.mic.background_color = (0, 0.35, 0.5, 1) if self.listening else DARK

        def toast(self, t):
            self.home.status.text = t

        def listen(self):
            self.listening = True
            self.home.status.text = "Listening..."
            self.stt.listen(pref_get("lang", "en-IN"), lambda t, e: Clock.schedule_once(lambda dt: self._heard(t, e)))

        def _heard(self, text, err):
            self.listening = False
            self.home.status.text = "Ready"
            if text:
                self.user_input(text)
            elif err:
                self.toast(err)

        def user_input(self, text):
            text = text.strip()
            if not text:
                return
            self.chat.add(text, True)
            SESSION.append({"role": "user", "content": text})
            if mem_on():
                msg_add("user", text)
            self.busy = True
            self.home.status.text = "Thinking..."
            threading.Thread(target=self._work, args=(text,), daemon=True).start()

        def _work(self, text):
            try:
                r = route(text)
                if r and r[1]:
                    Clock.schedule_once(lambda dt: self.confirm(r[0], r[1]))
                    return
                reply = r[0] if r else ai_reply()
            except RuntimeError as e:
                reply = offline_reply(text) if str(e) == "no_ai" else "Error: %s" % e
            except Exception as e:
                reply = "Error: %s" % e
            Clock.schedule_once(lambda dt: self.bot_say(reply))

        def bot_say(self, reply, speak=True):
            self.busy = False
            self.home.status.text = "Ready"
            self.home.last.text = reply[:300]
            self.chat.add(reply, False)
            SESSION.append({"role": "assistant", "content": reply})
            if mem_on():
                msg_add("assistant", reply)
            if speak and pref_get("speak", "1") == "1":
                self.tts.speak(reply)

        def confirm(self, text, fn, thread=True):
            self.busy = False
            box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
            l = Label(text=text, color=(0.85, 0.97, 1, 1), halign="center")
            l.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
            box.add_widget(l)
            row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
            p = popup("Confirm", box, (0.88, 0.4))

            def yes(*_):
                p.dismiss()
                if not thread:
                    return fn()

                def w():
                    try:
                        r = fn()
                    except Exception as e:
                        r = "Error: %s" % e
                    if isinstance(r, str):
                        Clock.schedule_once(lambda dt: self.bot_say(r))
                threading.Thread(target=w, daemon=True).start()
            row.add_widget(NeonButton(text="NO", on_release=lambda *_: p.dismiss()))
            row.add_widget(NeonButton(text="YES", on_release=yes))
            box.add_widget(row)
            p.open()

    JarvisApp().run()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--init" in sys.argv:
        init_build_files()
    else:
        run_ui()
