# Reisülküttab for Windows

Reisülküttab is a local-first Windows system-tray application for push-to-talk dictation, meeting capture, and optional transcript cleanup with local or hosted AI providers.

- Hold the configured dictation shortcut, speak, and release. Reisülküttab transcribes, optionally cleans the text, and pastes it at the cursor.
- Start and stop a meeting from the tray. Reisülküttab records the microphone and system output, then saves a Markdown note with the summary, decisions, action items, transcript, and adjacent WAV file.
- No account, cloud storage, telemetry, or background upload exists. Local mode keeps audio and transcript processing on the machine. Hosted transcription or cleanup sends only the required audio or text to the provider you selected.

## Install the Windows app

### Packaged build

1. Open PowerShell in the project directory.
2. Build the application:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\build-windows.ps1
   ```

3. Install and launch it:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install-windows.ps1
   ```

4. Launch **Reisülküttab** from the Start menu, open **Settings**, select a transcription provider, and configure the shortcuts.
5. Enable **Launch at login** from the tray if wanted.

The installer copies the complete onedir package to `%LOCALAPPDATA%\Programs\reisulkuttab`, creates the Start menu shortcut with the branded icon, removes predecessor and transitional installs, and preserves the existing user profile. Do not copy only the EXE: the adjacent `_internal` directory is required.

### Update

Give your coding agent this request:

> Update my Reisülküttab installation from `https://github.com/kemalcanyapali/reisulkitab`. Use the existing clone if present; otherwise clone the repository. Pull `main` with `git pull --ff-only`, run `build-windows.ps1`, then run `install-windows.ps1`. Preserve my settings and user data under `%APPDATA%\reisulkuttab` and `%LOCALAPPDATA%\reisulkuttab`.

The equivalent manual commands, run inside the repository, are:

```powershell
git pull --ff-only
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-windows.ps1
```

The install script replaces only the application under `%LOCALAPPDATA%\Programs\reisulkuttab`; settings, history, models, and recordings are retained.


### Run from source

Requirements: Windows 10/11, Python 3.11 or 3.12, and PowerShell.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-windows.txt
python reisulkuttab.py
```

`build-windows.ps1` creates the virtual environment and installs the Windows dependencies automatically when needed.

## Windows permissions

Reisülküttab does not require administrator access.

### Microphone

Enable both controls under **Settings → Privacy & security → Microphone**:

- **Microphone access**
- **Let desktop apps access your microphone**

If either control is disabled, dictation and the microphone side of meeting capture cannot start.

### Accessibility and Input Monitoring

Windows has no macOS-style Accessibility or Input Monitoring permission prompt for this workflow. Reisülküttab registers its global shortcuts with Windows and pastes with standard Windows input APIs.

Windows prevents a normal process from injecting input into an elevated process. If paste works in ordinary applications but not in an application launched as Administrator, run both applications at the same privilege level. Do not run Reisülküttab as Administrator solely to work around this boundary.

### System audio

Meeting capture uses Windows WASAPI loopback for speaker output. It normally needs no separate permission. Protected/DRM audio and some exclusive-mode devices may not expose a loopback stream.

## Dictation workflow

1. Configure **Settings → Shortcuts → Dictation**. The default Windows shortcut is `Ctrl+Alt+D`.
2. Hold the shortcut while speaking.
3. Release it to transcribe and paste.
4. A compact status pill shows recording and processing state.
5. Use the tray toggle to disable or enable the hotkeys without quitting.

The original clipboard contents are restored after paste. If another application changes the clipboard while Reisülküttab is restoring it, the newer clipboard content is preserved instead of being overwritten.

## Meeting workflow

1. Open the tray menu and choose **Start meeting**, or use the configured meeting shortcut.
2. Reisülküttab records microphone input and speaker output as separate channels.
3. Stop the meeting from the tray or with the same shortcut.
4. Each channel is transcribed, the timeline is interleaved, and the selected meeting provider produces:
   - a five-bullet summary;
   - decisions made;
   - action items with owners;
   - the full transcript below a divider.
5. The Markdown note and WAV recording are saved together under `%LOCALAPPDATA%\reisulkuttab\recordings` by default.

Long meetings may be divided into provider-safe chunks. Local transcription can work entirely offline after the selected whisper.cpp binary and model have been downloaded.

## Providers and privacy

### Local/offline

Choose **Local** as the transcription provider to use whisper.cpp. Choose **Local** as the cleanup provider to use llama.cpp. The Settings window downloads the selected binaries and models from their published release sources and verifies available checksums.

After the downloads complete, local dictation does not need a network connection. Local meeting summarization also requires a downloaded local LLM model.

### Hosted APIs

OpenAI, Groq, and OpenRouter are optional transcription providers. OpenAI-compatible endpoints can also be configured through the base URL fields.

API keys entered in Settings are encrypted for the current Windows user with DPAPI before being written to disk. An optional `.env` file can be placed at `%APPDATA%\reisulkuttab\.env`; see `.env.example`.

### Subscription CLIs

Claude Code, Codex, Antigravity, and compatible local command-line tools can be selected for cleanup or meeting notes when installed. They run as local child processes with the configured working directory and receive transcript text through standard input or a temporary prompt file. Reisülküttab does not bypass their authentication or usage limits.

## Files and settings

| Purpose | Windows location |
|---|---|
| Settings and optional `.env` | `%APPDATA%\reisulkuttab` |
| History, recordings, models, binaries, and cache | `%LOCALAPPDATA%\reisulkuttab` |
| Installed application | `%LOCALAPPDATA%\Programs\reisulkuttab` |

An existing profile from the predecessor application is moved to these directories on the first Reisülküttab launch. Settings, history, downloaded models, and recordings are retained.

## Command line

The packaged executable also exposes the command-line interface:

```powershell
Reisulkuttab.exe --help
Reisulkuttab.exe status --json
Reisulkuttab.exe record --seconds 8 --json
Reisulkuttab.exe transcribe .\meeting.mp4 --srt -o .\meeting.srt
Reisulkuttab.exe config get transcribe_provider --json
```

Commands that use the microphone communicate with the running tray instance. File transcription, settings, history, and diagnostics can run directly.

## Uninstall

1. Quit Reisülküttab from the tray.
2. Disable **Launch at login** before removing the application, or delete the `Reisülküttab` value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
3. Delete `%LOCALAPPDATA%\Programs\reisulkuttab`.
4. To remove personal data as well, delete `%APPDATA%\reisulkuttab` and `%LOCALAPPDATA%\reisulkuttab`.

## License

Reisülküttab is distributed under the GNU General Public License v3.0. The full, unmodified license is in [`LICENSE`](LICENSE).

This is a modified version of Dikte; the modifications are dated 2026. Source distributions remain licensed under GPL-3.0.
