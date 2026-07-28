# Video2Notes demo media

`evidence-demo.mp4` is a self-authored, redistributable 9-second fixture. It
contains three stable text states at 0, 3, and 6 seconds plus a quiet synthetic
tone. The large text and full-frame color changes exercise adaptive visual
change detection, OCR, screenshot selection, PTS alignment, and deterministic
Markdown/HTML/PDF rendering without an account, network request, API key, or
third-party copyrighted media.

Regenerate it on Windows with the repository's FFmpeg requirement:

```powershell
.\scripts\create_demo_media.ps1
```

The generated file is released under the repository MIT license. Its expected
properties are H.264/AAC, 640×360, 12 fps, 9 seconds.
