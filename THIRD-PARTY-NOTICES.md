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

## Python

Better Comfy includes the Python runtime. Copyright (c) 2001 Python Software Foundation, PSF License Agreement.
https://docs.python.org/3/license.html

## PyInstaller

The program is packed with PyInstaller. Its bootloader is licensed under the GNU General Public License version 2 with
an exception that allows it to be used for any program. https://github.com/pyinstaller/pyinstaller

## .NET Framework

The setup runs on the .NET Framework 4.8 that comes with Windows. Nothing of it is included.
