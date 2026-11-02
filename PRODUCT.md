# Product

<!-- impeccable:product-schema 1 -->

## Platform

Windows desktop (Python/PyQt6; not a web or mobile application).

## Product Purpose

Reisülküttab provides shortcut-driven dictation, meeting capture, file transcription and transcript cleanup from a Windows system-tray application.

## Capabilities and Constraints

- Preserve existing model/provider selection, credentials, configuration, recordings and history.
- Preserve recording, transcription, cleanup and meeting behavior; keep their existing model integrations.
- Keep eight settings pages. The user removed the desktop Agent page, tray actions and hotkey; existing CLI commands and saved configuration remain intact.
- Offer explicit update checking and user-triggered Windows installation; never interrupt active work or replace profile/model data.
- Keep keyboard navigation, screen fitting and non-focus-stealing recording indicators.
- Local processing and optional hosted providers retain their existing privacy boundaries; the UI must not claim that all processing is always local.

## Brand Commitments

Retain the Reisülküttab name and assets. The user explicitly chose anomalyco/hex as the visual reference and authorized building and updating the local installed application after verification.

## Evidence on Hand

README.md documents features, provider/privacy boundaries and installation. settings_ui.py implements settings; overlay.py implements recording/status indicators. Hex Windows reference: https://github.com/anomalyco/hex/blob/dev/tests/golden/rune-windows-settings-light.png.
