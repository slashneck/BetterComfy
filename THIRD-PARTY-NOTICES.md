# Third party notices

Better Comfy itself is released under the GNU General Public License version 3 (see `LICENSE`). It ships with or is
built on the following projects.

## ComfyUI

Better Comfy works with ComfyUI, which you install yourself (for example with Pinokio). ComfyUI is not included and is
not modified. It is released under the GNU General Public License version 3: https://github.com/comfyanonymous/ComfyUI

## Qt and Qt for Python (PySide6)

The window is built with Qt 6 through PySide6, both licensed under the GNU Lesser General Public License version 3.
They are included as separate libraries (`_internal\PySide6`) that can be replaced.

- Qt: https://www.qt.io/licensing, source: https://code.qt.io
- PySide6: https://wiki.qt.io/Qt_for_Python, source: https://code.qt.io/cgit/pyside/pyside-setup.git

## FFmpeg

Videos are written by `ffmpeg.exe`, which runs as a separate program. The included binary is the FFmpeg 7.1 "essentials"
build made by gyan.dev, as shipped by imageio-ffmpeg, licensed under the GNU General Public License version 3.

- FFmpeg source code: https://ffmpeg.org/download.html
- Build details: https://www.gyan.dev/ffmpeg/builds/

FFmpeg is not modified by Better Comfy.

## imageio-ffmpeg

Copyright (c) 2019-2025 imageio-ffmpeg contributors, BSD 2-Clause license. https://github.com/imageio/imageio-ffmpeg

## NumPy

Copyright (c) 2005-2025 NumPy Developers, BSD 3-Clause license. https://github.com/numpy/numpy

## Pillow

Copyright (c) 1997-2011 Secret Labs AB, (c) 1995-2011 Fredrik Lundh and contributors, (c) 2010 Jeffrey A. Clark and
contributors. MIT-CMU license. https://github.com/python-pillow/Pillow

## cryptography

Used for AES-256-GCM and HKDF in the vault. Copyright (c) Individual contributors, dual licensed under the Apache
License 2.0 and the BSD 3-Clause license. https://github.com/pyca/cryptography

## argon2-cffi

Used for Argon2id in the vault. Copyright (c) 2015 Hynek Schlawack and the argon2-cffi contributors, MIT license.
https://github.com/hynek/argon2-cffi. It includes the Argon2 reference implementation (CC0 1.0 / Apache License 2.0).

## Tag list

`assets\tags\danbooru.csv` is the Danbooru tag list from a1111-sd-webui-tagcomplete,
Copyright (c) 2022 Dominik Reh, MIT license. https://github.com/DominikDoom/a1111-sd-webui-tagcomplete

## Downloaded only when you set up the prompt helper

These are not included. If you set up the prompt helper in Settings, Better Comfy downloads them to the folder you pick
and checks them against a fixed SHA-256 checksum.

- llama.cpp (the runtime, Windows CPU build), MIT license. https://github.com/ggml-org/llama.cpp
- Qwen3.5 2B, 4B or 9B uncensored (Huihui abliterated) in GGUF format, Apache License 2.0. Models by the Qwen
  team, https://huggingface.co/Qwen, uncensored by huihui-ai, https://huggingface.co/huihui-ai, converted by
  mradermacher, https://huggingface.co/mradermacher
- TIPO v2.1 1B-A200M in GGUF format by KBlueLeaf, Kohaku License 1.0 (free for personal use).
  https://huggingface.co/KBlueLeaf/TIPO-v2.1-1B-A200M
- Qwen3.5 0.8B, 2B or 4B (the standard models of version 1.1.0), Apache License 2.0, converted by Unsloth,
  https://huggingface.co/unsloth

## Downloaded only when you ask for missing model parts

For Krea 2, Better Comfy can fetch its text encoder (qwen3vl_4b) and VAE (qwen_image_vae) on request from the
official Comfy-Org repository, https://huggingface.co/Comfy-Org/Krea-2, into ComfyUI's model folders. They are under
their own licenses and are not included.

## Python

Better Comfy includes the Python runtime. Copyright (c) 2001 Python Software Foundation, PSF License Agreement.
https://docs.python.org/3/license.html

## PyInstaller

The program is packed with PyInstaller. Its bootloader is licensed under the GNU General Public License version 2 with
an exception that allows it to be used for any program. https://github.com/pyinstaller/pyinstaller

## .NET Framework

The setup runs on the .NET Framework 4.8 that comes with Windows. Nothing of it is included.
