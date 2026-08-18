# Reisülküttab for Windows

Reisülküttab, konuşmayı yazıya çeviren, isteğe bağlı olarak metni temizleyip imleçteki uygulamaya yapıştıran bir Windows sistem tepsisi uygulamasıdır. Tepsiden toplantı başlatıp durdurabilir; mikrofon ve sistem sesini kaydedebilir, ardından özet, kararlar, aksiyonlar ve transkript içeren bir Markdown notu oluşturabilirsiniz.

Uygulama **local-first** çalışır: yerel sağlayıcılar ses ve metin işlemesini makinenizde tutar. İsterseniz OpenAI, Groq veya OpenRouter gibi barındırılan sağlayıcıları seçebilirsiniz; bu durumda yalnızca seçtiğiniz işlemin gerektirdiği ses veya metin o sağlayıcıya gönderilir.

Tam İngilizce başvuru için [English README](README.md) dosyasına bakın.

## Gereksinimler

- Windows 10 veya Windows 11
- Python 3.11 veya 3.12 (`py` başlatıcısı ile)
- PowerShell

## Derleme ve kurulum

PowerShell'de aşağıdaki komutları çalıştırın:

```powershell
git clone https://github.com/kemalcanyapali/reisulkitab.git
cd reisulkuttab
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-windows.ps1
```

Kurulum uygulamayı başlatır. Daha sonra Başlat menüsünden **Reisülküttab**'ı açın, **Ayarlar**'dan transkripsiyon sağlayıcısını ve kısayolları seçin. Paketlenmiş uygulamayı yalnızca EXE dosyasını kopyalayarak taşımayın; yanındaki `_internal` klasörü gereklidir.

## Güncelleme

Kodlama agentınıza şu isteği verin:

> Reisülküttab kurulumumu `https://github.com/kemalcanyapali/reisulkitab` adresinden güncelle. Mevcut klonu varsa kullan, yoksa depoyu klonla. `main` dalını `git pull --ff-only` ile çek; ardından `build-windows.ps1` ve `install-windows.ps1` dosyalarını çalıştır. `%APPDATA%\reisulkuttab` ve `%LOCALAPPDATA%\reisulkuttab` altındaki ayarlarımı ve kullanıcı verilerimi koru.

Aynı işlemin depo klasöründe çalıştırılacak manuel komutları:

```powershell
git pull --ff-only
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-windows.ps1
```

Kurulum betiği yalnızca `%LOCALAPPDATA%\Programs\reisulkuttab` altındaki uygulamayı değiştirir; ayarlar, geçmiş, modeller ve kayıtlar korunur.


## Mikrofon gizliliği

**Ayarlar → Gizlilik ve güvenlik → Mikrofon** altında şu iki denetimi etkinleştirin:

- **Mikrofon erişimi**
- **Masaüstü uygulamalarının mikrofonunuza erişmesine izin ver**

Bu denetimlerden biri kapalıysa dikte ve toplantı kaydının mikrofon bölümü başlayamaz. Reisülküttab yönetici izni gerektirmez.

## Sağlayıcılar ve gizlilik

**Yerel** transkripsiyon için whisper.cpp, metin temizleme için llama.cpp seçilebilir. Ayarlar penceresi gereken ikili dosyaları ve modelleri indirir; indirmeler tamamlandıktan sonra yerel dikte internet bağlantısı gerektirmez. Yerel toplantı özeti için ayrıca yerel bir LLM modeli gerekir.

Barındırılan sağlayıcılar isteğe bağlıdır. Ayarlara girilen API anahtarları geçerli Windows kullanıcısı için DPAPI ile şifrelenerek diske yazılır. İsteğe bağlı ortam dosyasını `%APPDATA%\reisulkuttab\.env` yoluna koyun; örnek için [`.env.example`](.env.example) dosyasına bakın.

## Dosya konumları

| Amaç | Windows konumu |
|---|---|
| Ayarlar ve isteğe bağlı `.env` | `%APPDATA%\reisulkuttab` |
| Geçmiş, kayıtlar, modeller, ikili dosyalar ve önbellek | `%LOCALAPPDATA%\reisulkuttab` |
| Toplantı notları ve WAV kayıtları | `%LOCALAPPDATA%\reisulkuttab\recordings` |
| Kurulu uygulama | `%LOCALAPPDATA%\Programs\reisulkuttab` |

## Lisans

Reisülküttab, GNU Genel Kamu Lisansı sürüm 3.0 (GPL-3.0) ile dağıtılır. Tam ve değiştirilmemiş lisans metni [`LICENSE`](LICENSE) dosyasındadır.

Bu, Dikte'nin değiştirilmiş bir sürümüdür; değişiklikler 2026 tarihlidir. Kaynak dağıtımları GPL-3.0 ile lisanslı kalır.
