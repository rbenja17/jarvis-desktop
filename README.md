# JARVIS

Asistente personal de voz para Windows 11 con Gemini Live y Flash-Lite. Corre localmente en tu PC usando tus propias claves API.

Desarrollado por **RIBN Digital**.

## Funciones

- Activación local por voz (`hey jarvis` con openWakeWord/ONNX) y atajo global (`Ctrl+Alt+J`)
- Conversación por voz en tiempo real con Gemini Live, con respaldo automático en Flash-Lite + Edge TTS
- Control de Windows: abrir y enfocar apps, volumen general y por aplicación, cambio de salida de audio, Bluetooth, brillo, energía con confirmación, capturas y automatización de pantalla por UI Automation
- Integraciones opcionales: Spotify Premium (PKCE), Google Calendar / Gmail (solo lectura) / Tasks, bot de Telegram y servidor HTTP local para Atajos de Siri
- Orbe flotante, ícono en la bandeja, ventana de Configuración, entrenamiento de voz personal, rutinas, recordatorios y resumen diario

## Instalación en Windows 11

### Opción 1: Instalador o versión portable (sin instalar Python)

1. Descargá `JarvisSetup-<versión>.exe` o `Jarvis-<versión>-portable.zip` desde [Releases](https://github.com/rbenja17/jarvis-desktop/releases).
2. Ejecutá el instalador y abrí **JARVIS**. En el primer arranque se abre la ventana de Configuración.
3. Generá una clave gratuita en [aistudio.google.com/apikey](https://aistudio.google.com/apikey), pegala en `GEMINI_API_KEY`, cargá tu ciudad para el clima y guardá.

En la versión instalada, tus claves, configuración, perfil, memoria y registros quedan en `%APPDATA%\JARVIS\`.

### Opción 2: Desde el código fuente (Python 3.12)

```powershell
git clone https://github.com/rbenja17/jarvis-desktop.git
cd jarvis-desktop
scripts\instalar.bat
```

O manualmente:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy config.example.toml config.toml
.venv\Scripts\python.exe -m jarvis
```

En modo desarrollo, `.env`, `config.toml` y `data/` se guardan en la raíz del proyecto y están ignorados por Git.

## Integraciones opcionales

Desde **Ícono en la bandeja -> Configuración -> Integraciones**:

- **Spotify**: creá una app en [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) con Redirect URI `http://127.0.0.1:8888/callback`, pegá tu `SPOTIFY_CLIENT_ID` y hacé clic en **Vincular Spotify**.
- **Google**: creá un cliente OAuth de escritorio en [console.cloud.google.com](https://console.cloud.google.com) con las APIs de Calendar, Gmail y Tasks habilitadas, importá el JSON y hacé clic en **Vincular Google**.
- **Telegram**: creá un bot con `@BotFather`, pegá el token, enviale `/start` para ver tu `chat_id` y cargalo en `TELEGRAM_CHAT_IDS`.
- **Siri / Red local**: generá un token en Configuración y enviá un `POST` con `Authorization: Bearer <token>` y JSON `{"texto": "..."}` a `http://<ip-pc>:8765/comando`.

## Comandos y pruebas

```powershell
.venv\Scripts\python.exe -m jarvis --texto "subí el volumen al 40"
.venv\Scripts\python.exe -m jarvis --sin-interfaz
.venv\Scripts\python.exe -m jarvis --selftest
.venv\Scripts\python.exe -m pytest
```

## Compilar el ejecutable

```powershell
.venv\Scripts\python.exe -m pip install pyinstaller
.\packaging\build.ps1 -Version "0.1.0"
```

Genera `dist\Jarvis\Jarvis.exe`, `dist\Jarvis-0.1.0-portable.zip` y `dist\installer\JarvisSetup-0.1.0.exe` (si tenés Inno Setup 6 instalado).

## Privacidad

Las claves API, tokens OAuth, historial y `data/perfil.md` permanecen en tu PC. El audio solo se envía a tu propia clave de Gemini después de activar JARVIS.

## Licencia

[MIT](LICENSE)
