"""Who rewrites the transcript once it has been heard.

Normally a small local model, OpenRouter, Claude Code, or Antigravity can do
the job. Subscription-backed CLIs avoid a second key or bill, at the cost of
starting a whole CLI session for each cleanup.

Whoever does it, the job is the same one: no tools, no files, no memory of the
last dictation. There is nothing here to look up and nothing to carry over, and
a transcript is text from a microphone rather than an instruction, so the less
the agent can reach while it reads one, the better.
"""

import os
import pathlib
import shutil
import subprocess
import tempfile
import threading

import api
import assistant
import ggml
import process
from i18n import t

PROVIDERS = ("local", "agy", "openrouter", "claude")

_active_lock = threading.Lock()
_active_processes = set()


def stop_all():
    """Terminate subscription CLI trees still owned by cleanup workers."""
    with _active_lock:
        processes = tuple(_active_processes)
    for proc in processes:
        assistant._kill(proc)


class CleanupError(api.ApiError):
    """What a CLI could not do.

    An ApiError because to the chain a cleanup that failed is a cleanup that
    failed, whichever way it was run, and every caller already catches one and
    keeps the raw transcript.
    """


def provider(conf):
    chosen = conf["cleanup_provider"]
    return chosen if chosen in PROVIDERS else "local"


def executable(name):
    """The CLI a provider runs, or "" when it needs none."""
    if name == "agy":
        return _agy_binary()
    return "claude" if name == "claude" else ""


def model(conf):
    """Which model does the cleaning, for the history and the settings window."""
    name = provider(conf)
    if name == "local":
        return conf["local_llm_model"]
    if name == "claude":
        return conf["cleanup_claude_model"].strip() or "haiku"
    if name == "agy":
        return conf["cleanup_agy_model"].strip() or "Antigravity default"
    return conf["cleanup_model"]


def run(text, conf, system_prompt, timeout=180, aborter=None,
        provider_name=None, model_name=None, reasoning=None):
    """Hand text to the configured local, hosted, or subscription-backed model."""
    name = provider_name or provider(conf)
    if name not in PROVIDERS:
        name = "local"
    effort = conf["cleanup_reasoning"] if reasoning is None else reasoning
    if name == "openrouter":
        return api.cleanup(
            text, conf.openrouter_key(), model_name or conf["cleanup_model"],
            system_prompt, reasoning=effort,
            base_url=conf["openrouter_base_url"], timeout=timeout,
            aborter=aborter,
        )
    if name == "local":
        return _local(text, conf, system_prompt, timeout, aborter)
    if name == "claude":
        return _claude(text, conf, system_prompt, timeout, model_name, effort)
    return _agy(text, conf, system_prompt, timeout, model_name)


def _local(text, conf, system_prompt, timeout, aborter=None):
    """llama.cpp, on this machine, answering the request OpenRouter answers.

    No key and no bill, and the address does not exist until the server is up,
    which is what starting it here is for. The timeout is the hosted one raised:
    the only thing being spent is time.
    """
    service = t("Local model")
    try:
        return api.cleanup(
            text, "", conf["local_llm_model"], system_prompt,
            reasoning=conf["local_llm_reasoning"],
            base_url=api.serving(ggml.llm),
            timeout=max(timeout, api.LOCAL_TIMEOUT),
            provider="local-llm", service=service, aborter=aborter,
        )
    except api.ApiError as exc:
        # A server that died mid-request would otherwise report only that the
        # connection dropped, when the reason is in its own output.
        raise api.local_failure(service, ggml.llm, exc) from None


def _wrap(text):
    """The same fence the OpenRouter call puts around it: this is the material,
    not the instruction, however much of it reads like one."""
    return f"<transcript>\n{text}\n</transcript>"


# --- Claude Code ----------------------------------------------------------

def _claude(text, conf, system_prompt, timeout, model_name=None, reasoning=None):
    cmd = [
        "claude", "-p", _wrap(text),
        "--system-prompt", system_prompt,
        "--output-format", "text",
        "--permission-mode", "dontAsk",
        "--tools", "",
        "--disable-slash-commands",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--model", model_name or conf["cleanup_claude_model"].strip() or "haiku",
    ]
    effort = assistant.CLAUDE_EFFORT.get(
        conf["cleanup_reasoning"] if reasoning is None else reasoning, "")
    if effort:
        cmd += ["--effort", effort]

    answer = _output(cmd, timeout, "Claude")
    if not answer:
        raise CleanupError(t("{service} answered with nothing.", service="Claude"))
    return answer




# --- Antigravity -----------------------------------------------------------

def _agy_binary():
    candidates = []
    if os.name == "nt":
        candidates.append(os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe"))
    found = shutil.which("agy") or ""
    if found and pathlib.Path(found).resolve().parent != pathlib.Path.cwd().resolve():
        candidates.append(found)
    return next((str(pathlib.Path(path).resolve()) for path in candidates
                 if path and os.path.isfile(path)), "")


def _agy(text, conf, system_prompt, timeout, model_name=None):
    body = (
        f"{system_prompt}\n\nDo not use tools or read files. Return only the "
        f"requested text.\n\n---\n\n{_wrap(text)}"
    )
    cmd = [
        _agy_binary(), "--sandbox", "--disable-slash-commands",
        "--mode", "plan", "-p", body,
    ]
    chosen_model = model_name or conf["cleanup_agy_model"].strip()
    if chosen_model:
        cmd += ["--model", chosen_model]
    workdir = ggml.DATA_DIR / "agy-sandbox"
    workdir.mkdir(parents=True, exist_ok=True)
    answer = _output(cmd, timeout, "Antigravity", cwd=str(workdir))
    if not answer:
        raise CleanupError(t(
            "{service} answered with nothing.", service="Antigravity"))
    return answer


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


# --- running a CLI --------------------------------------------------------
def _redirected_output(cmd, timeout, service, cwd):
    """Run a Windows CLI in a kill-on-close job with file-backed output."""
    proc = None
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            proc = subprocess.Popen(
                cmd, cwd=cwd or os.path.expanduser("~"), stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr,
                env=assistant.cli_environment(),
                **process.windowless_options(),
            )
            try:
                try:
                    ggml._assign_kill_job(proc)
                except OSError:
                    assistant._kill(proc)
                    raise
                with _active_lock:
                    _active_processes.add(proc)
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                assistant._kill(proc)
                raise CleanupError(t(
                    "{service} did not finish within {seconds} seconds.",
                    service=service, seconds=timeout)) from None
            finally:
                with _active_lock:
                    _active_processes.discard(proc)
                ggml._close_job(proc)
            stdout.seek(0)
            stderr.seek(0)
            return (
                proc.returncode,
                stdout.read().decode("utf-8", "replace"),
                stderr.read().decode("utf-8", "replace"),
            )
    except OSError as exc:
        raise CleanupError(t("Could not run {binary}: {error}",
                             binary=cmd[0], error=exc)) from exc



def _output(cmd, timeout, service, cwd=None):
    """Run cmd to the end and return what it printed."""
    binary = assistant.resolved_executable(cmd[0])
    if not binary:
        raise CleanupError(t(
            "{binary} not found. Install it, or have OpenRouter clean up "
            "instead, under Settings → API and models.", binary=cmd[0] or service,
        ))
    cmd = [binary, *cmd[1:]]
    if os.name == "nt":
        code, stdout, stderr = _redirected_output(cmd, timeout, service, cwd)
        if code != 0:
            raise CleanupError(assistant.last_line(stderr) or t(
                "{service} exited with code {code}.",
                service=service, code=code))
        return stdout.strip()
    try:
        done = subprocess.run(
            cmd, cwd=cwd or os.path.expanduser("~"), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=assistant.cli_environment(),
        )
    except subprocess.TimeoutExpired:
        raise CleanupError(t("{service} did not finish within {seconds} seconds.",
                             service=service, seconds=timeout)) from None
    except OSError as exc:
        raise CleanupError(t("Could not run {binary}: {error}",
                             binary=binary, error=exc)) from exc
    if done.returncode != 0:
        raise CleanupError(assistant.last_line(done.stderr) or t(
            "{service} exited with code {code}.",
            service=service, code=done.returncode))
    return (done.stdout or "").strip()
